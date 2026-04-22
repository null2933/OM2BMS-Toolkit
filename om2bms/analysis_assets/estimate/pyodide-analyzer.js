/**
 * pyodide-analyzer.js
 * PyodideとNumPyを使用したBMS解析エンジンのラッパー
 */
export class BMSPyAnalyzer {
    constructor() {
        this.pyodide = null;
        this.pythonAnalyzer = null;
    }

    /**
     * Pyodideの初期化とNumPyのロード、Pythonクラスの定義
     */
    async init() {
        if (this.pyodide) return; // すでに初期化済みの場合はスキップ

        // 1. Pyodide本体のロード
        this.pyodide = await loadPyodide();
        
        // 2. NumPyパッケージのロード
        await this.pyodide.loadPackage("numpy");

        // 3. Pythonクラス定義の実行
        await this.pyodide.runPythonAsync(`
import numpy as np
import math

class SongInfo:
    def __init__(self, title, subtitle, artist, subartist, song_last_ms, total, total_notes):
        self.title = title
        self.subtitle = subtitle
        self.artist = artist
        self.subartist = subartist
        self.song_last_ms = song_last_ms
        self.total = total
        self.total_notes = total_notes

class BMS:
    TIME_LIMIT = 400.0

    def __init__(self, timeline_master, song_info_dict):
        s = song_info_dict.to_py()

        self.song_info = SongInfo(
            title=s['title'],
            subtitle=s['subtitle'],
            artist=s['artist'],
            subartist=s['subartist'],
            song_last_ms=s['song_last_ms'],
            total=s['total'],
            total_notes=s['total_notes']
        )

        self.timeline_master = np.array(timeline_master.to_py())
        self.set_meta_master()

    def set_meta_master(self):
        times = self.timeline_master[:, 0]
        lane_data = self.timeline_master[:, 1:8]
        scratch_data = self.timeline_master[:, 8]
        all_notes = self.timeline_master[:, 1:9]
        num_events = len(times)

        max_ms = int(math.ceil(self.song_info.song_last_ms))
        ms_to_idx = np.zeros(max_ms + 2, dtype=np.int32)

        current_idx = 0
        for ms in range(max_ms + 1):
            while current_idx < num_events and times[current_idx] < ms:
                current_idx += 1
            ms_to_idx[ms] = current_idx
        ms_to_idx[max_ms + 1] = num_events

        def get_cumsum(arr):
            if arr.ndim == 1:
                return np.concatenate([[0], np.cumsum(arr)])
            return np.concatenate([np.zeros((1, arr.shape[1])), np.cumsum(arr, axis=0)], axis=0)

        button_count_per_evt = lane_data.sum(axis=1)
        scratch_count_per_evt = scratch_data
        notes_count_per_evt = all_notes.sum(axis=1)

        button_count_cum = get_cumsum(button_count_per_evt)
        scratch_count_cum = get_cumsum(scratch_count_per_evt)
        notes_count_cum = get_cumsum(notes_count_per_evt)
        simul_count_cum = get_cumsum(np.ones(num_events))

        def get_idx_safe(t):
            return ms_to_idx[max(0, min(int(t), max_ms))]

        button_moving_sum_at_evt = np.zeros(num_events)
        scratch_moving_sum_at_evt = np.zeros(num_events)
        for i in range(num_events):
            t = times[i]
            idx_s = get_idx_safe(t - 25)
            idx_e = get_idx_safe(t + 25)
            button_moving_sum_at_evt[i] = button_count_cum[idx_e] - button_count_cum[idx_s]
            scratch_moving_sum_at_evt[i] = scratch_count_cum[idx_e] - scratch_count_cum[idx_s]

        button_scratch_interaction_cum = get_cumsum(button_moving_sum_at_evt * scratch_count_per_evt)

        intervals = np.diff(times, prepend=times[0] - 100.0)

        is_fast = (intervals <= 40.0).astype(np.float32)
        is_fast[0] = 0

        movement_pure = np.abs(np.diff(lane_data, axis=0, prepend=0)).sum(axis=1)
        movement_pure = np.where(intervals <= BMS.TIME_LIMIT, movement_pure, 0.0)

        interval_diffs = np.abs(np.diff(intervals, prepend=intervals[0]))
        rhythm_jitter_step = np.where(intervals <= BMS.TIME_LIMIT, interval_diffs, 0.0)

        persistence_mask = np.zeros(num_events, dtype=np.bool_)
        if num_events > 1:
            persistence_mask[1:] = (is_fast[1:] > 0) & (is_fast[:-1] > 0)

        persistence_ms = np.where(persistence_mask, intervals, 0.0)

        delay_timing_cum = get_cumsum(is_fast)
        delay_movement_cum = get_cumsum(movement_pure * is_fast)
        rhythm_jitter_cum = get_cumsum(rhythm_jitter_step)
        delay_persistence_cum = get_cumsum(persistence_ms)

        chord_map = np.zeros((num_events, 9), dtype=np.float32)
        for c in range(1, 9):
            chord_map[:, c] = (notes_count_per_evt == c)
        chord_counts_cum = get_cumsum(chord_map)
        chord_counts_2d_cum = np.cumsum(chord_counts_cum, axis=1)

        size_deltas = np.where(
            intervals <= BMS.TIME_LIMIT,
            np.abs(np.diff(notes_count_per_evt, prepend=notes_count_per_evt[0])),
            0.0,
        )
        chord_size_delta_cum = get_cumsum(size_deltas)

        weights = 2 ** np.arange(8)
        chord_ids = (all_notes @ weights).astype(np.int32)
        chord_ids[notes_count_per_evt < 2] = 0

        v = np.diff(notes_count_per_evt, prepend=notes_count_per_evt[0])
        v = np.where(intervals <= BMS.TIME_LIMIT, v, 0.0)
        v_prev = np.roll(v, 1); v_prev[0] = 0
        oscillation_pure = np.where(v * v_prev < 0, np.abs(v * v_prev), 0.0)
        chord_oscillation_cum = get_cumsum(oscillation_pure)

        prev_size = np.roll(notes_count_per_evt, 1); prev_size[0] = 0
        heavy_hit_jitter_pure = np.where(intervals <= BMS.TIME_LIMIT, prev_size * rhythm_jitter_step, 0.0)
        heavy_hit_jitter_cum = get_cumsum(heavy_hit_jitter_pure)

        shape_flux_pure = np.where(intervals <= BMS.TIME_LIMIT, size_deltas * (1000.0 / (intervals + 10.0)), 0.0)
        shape_flux_cum = get_cumsum(shape_flux_pure)

        lane_jack_map = np.zeros((num_events, 7), dtype=np.float32)
        for j in range(7):
            active_idx = np.flatnonzero(lane_data[:, j])
            if active_idx.size > 1:
                dist = np.diff(times[active_idx])
                t_start = 140.0
                t_full = 130.0
                weight = np.clip((t_start - dist) / (t_start - t_full), 0, 1.0)
                lane_jack_map[active_idx[1:], j] = weight * (t_start - dist)

        total_jack_at_evt = lane_jack_map.sum(axis=1)
        lane_jack_cum = get_cumsum(lane_jack_map)

        is_jack_evt = (total_jack_at_evt > 0).astype(np.float32)

        jack_chord_size_pure = notes_count_per_evt * is_jack_evt
        jack_chord_size_sq_pure = (notes_count_per_evt**2) * is_jack_evt

        is_jack_evt_cum = get_cumsum(is_jack_evt)
        jack_chord_size_cum = get_cumsum(jack_chord_size_pure)
        jack_chord_size_sq_cum = get_cumsum(jack_chord_size_sq_pure)

        movement_cum = get_cumsum(movement_pure)
        movement_interaction_cum = get_cumsum(movement_pure * notes_count_per_evt)

        prev_lanes = np.roll(lane_data, 1, axis=0); prev_lanes[0] = 0
        stayed_count = np.where(
            intervals <= BMS.TIME_LIMIT,
            ((lane_data == 1) & (prev_lanes == 1)).sum(axis=1),
            0,
        )
        movement_stayed_cum = get_cumsum(movement_pure * stayed_count)

        stayed_count_cum = get_cumsum(stayed_count)

        def get_lag_cum(lag):
            pad = np.zeros((lag, 7))
            lagged_lane = np.vstack([pad, lane_data[:-lag]])
            lagged_time = np.concatenate([np.full(lag, -1e9), times[:-lag]])

            dt = times - lagged_time

            mov = np.abs(lane_data - lagged_lane).sum(axis=1)
            mov = np.where(dt <= BMS.TIME_LIMIT, mov, 0.0)
            return get_cumsum(mov)

        num_constrained = (lane_jack_map > 0).sum(axis=1)

        mov_sum_total = button_moving_sum_at_evt + scratch_moving_sum_at_evt
        jack_chord_conflict_delay_cum = get_cumsum(num_constrained * mov_sum_total)

        def get_centroid_drift(data, times, k, limit):
            num_events = len(times)
            acc_lane = np.zeros((num_events, 7), dtype=np.float32)
            acc_count = np.zeros(num_events, dtype=np.float32)

            for i in range(1, k + 1):
                lagged_lane = np.roll(data, i, axis=0)
                lagged_time = np.roll(times, i)

                mask = (times - lagged_time <= limit) & (np.arange(num_events) >= i)

                acc_lane += lagged_lane * mask[:, None]
                acc_count += mask

            safe_count = np.maximum(acc_count, 1.0)
            posture_p = acc_lane / safe_count[:, None]

            drift_step = np.abs(data - posture_p).sum(axis=1)
            drift_step = np.where(acc_count > 0, drift_step, 0.0)
            return drift_step

        centroid_drift_pure = get_centroid_drift(lane_data, times, k=4, limit=BMS.TIME_LIMIT)
        centroid_drift_cum = get_cumsum(centroid_drift_pure)

        h1 = movement_pure

        h2_pure = np.zeros(num_events)
        if num_events > 2:
            dt2 = times[2:] - times[:-2]
            dist2 = np.abs(lane_data[2:] - lane_data[:-2]).sum(axis=1)
            h2_pure[2:] = np.where(dt2 <= BMS.TIME_LIMIT, dist2, 0.0)

        triangle_gap_pure = np.zeros(num_events)
        if num_events > 2:
            triangle_gap_pure[2:] = (h1[2:] + h1[1:-1]) - h2_pure[2:]
            triangle_gap_pure = np.maximum(triangle_gap_pure, 0.0)

        triangle_gap_cum = get_cumsum(triangle_gap_pure)

        def get_cluster(threshold, limit):
            clustered_strike_starts = np.zeros(num_events, dtype=np.float32)
            clustered_strike_starts[0] = 1.0
            cluster_final_configs = []

            curr_start_idx = 0
            active_lanes = all_notes[0].copy()

            for i in range(1, num_events):
                dt = times[i] - times[curr_start_idx]
                has_lane_conflict = np.any((all_notes[i] > 0) & (active_lanes > 0))

                if dt <= threshold and not has_lane_conflict:
                    active_lanes = np.maximum(active_lanes, all_notes[i])
                else:
                    cluster_final_configs.append(active_lanes.copy())
                    clustered_strike_starts[i] = 1.0
                    curr_start_idx = i
                    active_lanes = all_notes[i].copy()

            cluster_final_configs.append(active_lanes)
            cluster_final_configs = np.array(cluster_final_configs)
            strike_indices = np.flatnonzero(clustered_strike_starts)
            M = len(strike_indices)
            stimes = times[strike_indices]
            s_keys = cluster_final_configs[:, :7]

            drift_s_pure = np.zeros(M, dtype=np.float32)
            gap_s_pure = np.zeros(M, dtype=np.float32)
            h1_s = np.zeros(M, dtype=np.float32)

            if M > 1:
                drift_s_pure = get_centroid_drift(s_keys, stimes, k=4, limit=limit)

                diffs_s = np.abs(np.diff(s_keys, axis=0, prepend=0)).sum(axis=1)
                dt1_s = np.diff(stimes, prepend=stimes[0] - 1000.0)
                h1_s = np.where(dt1_s <= limit, diffs_s, 0.0)

                if M > 2:
                    dt2_s = stimes[2:] - stimes[:-2]
                    dist2_s = np.abs(s_keys[2:] - s_keys[:-2]).sum(axis=1)
                    h2_s = np.where(dt2_s <= limit, dist2_s, 0.0)
                    gap_s_pure[2:] = np.maximum((h1_s[2:] + h1_s[1:-1]) - h2_s, 0.0)

            drift_strike_timeline = np.zeros(num_events, dtype=np.float32)
            gap_strike_timeline = np.zeros(num_events, dtype=np.float32)
            clustered_movement_pure = np.zeros(num_events, dtype=np.float32)
            lane_strike_timeline = np.zeros((num_events, 7), dtype=np.float32)

            drift_strike_timeline[strike_indices] = drift_s_pure
            gap_strike_timeline[strike_indices] = gap_s_pure
            clustered_movement_pure[strike_indices] = h1_s
            lane_strike_timeline[strike_indices] = s_keys

            cluster_sizes = cluster_final_configs.sum(axis=1)
            size_sum_pure = np.zeros(num_events, dtype=np.float32)
            size_sq_sum_pure = np.zeros(num_events, dtype=np.float32)
            size_sum_pure[strike_indices] = cluster_sizes
            size_sq_sum_pure[strike_indices] = cluster_sizes**2

            strike_jitter_pure = np.zeros(num_events, dtype=np.float32)
            if M > 2:
                s_intervals = np.diff(stimes)
                s_jitter = np.abs(np.diff(s_intervals, prepend=s_intervals[0]))
                strike_jitter_pure[strike_indices[1:]] = np.minimum(s_jitter, 400.0)

            return (
                get_cumsum(clustered_movement_pure), 
                get_cumsum(clustered_strike_starts), 
                get_cumsum(strike_jitter_pure),
                get_cumsum(size_sum_pure), 
                get_cumsum(size_sq_sum_pure),
                get_cumsum(drift_strike_timeline),
                get_cumsum(gap_strike_timeline),
                get_cumsum(lane_strike_timeline),
            )

        (
            clustered_movement_40_cum,
            clustered_strike_count_40_cum,
            clustered_strike_jitter_40_cum,
            clustered_strike_size_40_cum,
            clustered_strike_sizesq_40_cum,
            drift_strike_40_cum,
            gap_strike_40_cum,
            lane_strike_40_cum,
        ) = get_cluster(40, limit=BMS.TIME_LIMIT)


        def extract_stream_features_global(times, lane_data):
            num_events = len(times)

            ev_idx, l_idx = np.nonzero(lane_data[:, 0:7])
            if len(ev_idx) == 0:
                return np.zeros(num_events), np.zeros(num_events), np.zeros(num_events)

            t_idx = times[ev_idx]
            M = len(ev_idx)

            next_node = np.full(M, -1, dtype=np.int32)
            in_degree = np.zeros(M, dtype=np.int32)

            MAX_TIME = 100.0
            MAX_LANE = 4.0
            TIME_PENALTY = 0.015
            LOOKAHEAD = 8

            for i in range(M):
                end_idx = min(i + 1 + LOOKAHEAD, M)
                if i + 1 == end_idx:
                    continue

                c_t = t_idx[i+1 : end_idx]
                c_l = l_idx[i+1 : end_idx]
                c_used = in_degree[i+1 : end_idx]

                dt = c_t - t_idx[i]
                dist = np.abs(c_l - l_idx[i])

                valid_mask = (dt <= MAX_TIME) & (dist <= MAX_LANE) & (c_used == 0)

                if not np.any(valid_mask):
                    continue

                cost = np.where(valid_mask, dist + TIME_PENALTY * dt, np.inf)
                best_local_idx = np.argmin(cost)

                best_global_idx = i + 1 + best_local_idx
                next_node[i] = best_global_idx
                in_degree[best_global_idx] += 1

            active_streams_pure = np.zeros(num_events, dtype=np.float32)
            strokes_pure = np.zeros(num_events, dtype=np.float32)
            crossings_pure = np.zeros(num_events, dtype=np.float32)

            start_nodes = np.where(in_degree == 0)[0]
            edges = []

            for start in start_nodes:
                curr = start
                current_dir = 0
                while next_node[curr] != -1:
                    nxt = next_node[curr]

                    ev_u, ev_v = ev_idx[curr], ev_idx[nxt]
                    t_u, t_v   = t_idx[curr], t_idx[nxt]
                    l_u, l_v   = l_idx[curr], l_idx[nxt]

                    edges.append((ev_u, t_u, l_u, ev_v, t_v, l_v))

                    active_streams_pure[ev_u : ev_v] += 1

                    dist_val = l_v - l_u
                    if dist_val != 0:
                        new_dir = 1 if dist_val > 0 else -1
                        if current_dir != new_dir:
                            strokes_pure[ev_v] += 1
                            current_dir = new_dir

                    curr = nxt

            def ccw(tA, lA, tB, lB, tC, lC):
                return (tC - tA) * (lB - lA) > (tB - tA) * (lC - lA)

            def intersect(tA, lA, tB, lB, tC, lC, tD, lD):
                if min(tA, tB) > max(tC, tD) or min(tC, tD) > max(tA, tB):
                    return False
                return ccw(tA, lA, tC, lC, tD, lD) != ccw(tB, lB, tC, lC, tD, lD) and ccw(tA, lA, tB, lB, tC, lC) != ccw(tA, lA, tB, lB, tD, lD)

            edges.sort(key=lambda e: e[1])
            E_len = len(edges)

            for i in range(E_len):
                evA, tA, lA, evB, tB, lB = edges[i]
                for j in range(i + 1, E_len):
                    evC, tC, lC, evD, tD, lD = edges[j]
                    if tC > tB: 
                        break
                    if intersect(tA, lA, tB, lB, tC, lC, tD, lD):
                        cross_ev = max(evA, evC)
                        crossings_pure[cross_ev] += 1

            return active_streams_pure, strokes_pure, crossings_pure

        (
            stream_active_streams_pure,
            stream_strokes_pure,
            stream_crossings_pure
        ) = extract_stream_features_global(times, lane_data)

        stream_active_cum = get_cumsum(stream_active_streams_pure)
        stream_strokes_cum = get_cumsum(stream_strokes_pure)
        stream_crossings_cum = get_cumsum(stream_crossings_pure)


        self.meta_master = {
            "ms_to_idx": ms_to_idx,
            "max_ms": max_ms,
            "lane_count_cum": get_cumsum(lane_data),
            "notes_count_cum": notes_count_cum,
            "button_count_cum": button_count_cum,
            "scratch_count_cum": scratch_count_cum,
            "simul_count_cum": simul_count_cum,
            "button_scratch_interaction_cum": button_scratch_interaction_cum,
            "delay_timing_cum": delay_timing_cum,
            "delay_movement_cum": delay_movement_cum,
            "rhythm_jitter_cum": rhythm_jitter_cum,
            "delay_persistence_cum": delay_persistence_cum,
            "chord_counts_cum": chord_counts_cum,
            "chord_counts_2d_cum": chord_counts_2d_cum,
            "chord_size_delta_cum": chord_size_delta_cum,
            "chord_ids": chord_ids,

            "chord_oscillation_cum": chord_oscillation_cum,
            "heavy_hit_jitter_cum": heavy_hit_jitter_cum,
            "shape_flux_cum": shape_flux_cum,

            "movement_cum": movement_cum,
            "movement_interaction_cum": movement_interaction_cum,
            "movement_stayed_cum": movement_stayed_cum,
            "stayed_count_cum": stayed_count_cum,
            "movement_lag2_cum": get_lag_cum(2),
            "movement_lag3_cum": get_lag_cum(3),
            "movement_lag4_cum": get_lag_cum(4),
            "lane_jack_cum": lane_jack_cum,

            "is_jack_evt_cum": is_jack_evt_cum,
            "jack_chord_size_cum": jack_chord_size_cum,
            "jack_chord_size_sq_cum": jack_chord_size_sq_cum,

            "jack_chord_conflict_delay_cum": jack_chord_conflict_delay_cum,
            "intervals": intervals,

            "centroid_drift_cum": centroid_drift_cum,
            "triangle_gap_cum": triangle_gap_cum,

            "clustered_movement_40_cum": clustered_movement_40_cum,
            "clustered_strike_count_40_cum": clustered_strike_count_40_cum,
            "clustered_strike_jitter_40_cum": clustered_strike_jitter_40_cum,
            "clustered_strike_size_40_cum": clustered_strike_size_40_cum,
            "clustered_strike_sizesq_40_cum": clustered_strike_sizesq_40_cum,
            "drift_strike_40_cum": drift_strike_40_cum,
            "gap_strike_40_cum": gap_strike_40_cum,
            "lane_strike_40_cum": lane_strike_40_cum,

            "stream_active_cum": stream_active_cum,
            "stream_strokes_cum": stream_strokes_cum,
            "stream_crossings_cum": stream_crossings_cum,
        }

    def get_window_meta(self, time_start, time_end):
        mm = self.meta_master
        ms_to_idx = mm["ms_to_idx"]
        max_ms = mm["max_ms"]

        def get_idx(t):
            return ms_to_idx[max(0, min(int(t), max_ms))]

        idx_s = get_idx(time_start)
        idx_e = get_idx(time_end)

        def from_cum(key):
            return mm[key][idx_e] - mm[key][idx_s]

        f_button_count = from_cum("button_count_cum")
        f_scratch_count = from_cum("scratch_count_cum")

        f_recovery = self.song_info.total*(f_button_count+f_scratch_count)/self.song_info.total_notes

        idx_w2_s = get_idx(time_start - 2000)
        idx_w5_s = get_idx(time_start - 5000)
        f_wide_2_notes_count = mm["notes_count_cum"][idx_s] - mm["notes_count_cum"][idx_w2_s]
        f_wide_5_notes_count = mm["notes_count_cum"][idx_s] - mm["notes_count_cum"][idx_w5_s]

        f_button_scratch_interaction = from_cum("button_scratch_interaction_cum")

        lane_counts = from_cum("lane_count_cum")
        if f_button_count > 0:
            ratios = np.where(f_button_count > 0, lane_counts / f_button_count, 0.0)
            f_lane_bias_entropy = -np.sum(ratios * np.log(ratios + 1e-9))
        else:
            f_lane_bias_entropy = 0.0

        f_simul_count = from_cum("simul_count_cum")

        f_rhythm_jitter = np.log1p(
            from_cum("rhythm_jitter_cum") / f_simul_count if f_simul_count > 0 else 0.0
        )
        f_delay_persistence = from_cum("delay_persistence_cum")

        if f_simul_count > 0:
            f_chord_mean = (f_button_count + f_scratch_count) / f_simul_count
            f_chord_size_delta = from_cum("chord_size_delta_cum") / f_simul_count

            c_win_cum = mm["chord_counts_2d_cum"][idx_e, 1:9] - mm["chord_counts_2d_cum"][idx_s, 1:9]
            sizes = np.arange(1, 9)

            def get_p(p):
                v_idx = (f_simul_count - 1) * p
                low, high = math.floor(v_idx), math.ceil(v_idx)

                idx_l = min(np.searchsorted(c_win_cum, low + 1), 7)
                idx_h = min(np.searchsorted(c_win_cum, high + 1), 7)

                v_l = sizes[idx_l]
                v_h = sizes[idx_h]
                return v_l + (v_idx - low) * (v_h - v_l)

            f_chord_q1, f_chord_q2, f_chord_q3 = get_p(0.25), get_p(0.50), get_p(0.75)

            c_ids = mm["chord_ids"][idx_s:idx_e]
            if c_ids.size > 0:
                counts = np.bincount(c_ids, minlength=256)
                f_chord_pattern_count = np.count_nonzero(counts[1:])
            else:
                f_chord_pattern_count = 0.0

            if f_simul_count == 0:
                f_chord_pattern_count_per_simul = 0.0
            else:
                f_chord_pattern_count_per_simul = f_chord_pattern_count / f_simul_count

            f_oscillation = from_cum("chord_oscillation_cum") / f_simul_count
            f_heavy_hit_jitter = np.log1p(from_cum("heavy_hit_jitter_cum") / f_simul_count)
            f_shape_flux = from_cum("shape_flux_cum") / f_simul_count
        else:
            f_chord_mean = f_chord_size_delta = f_chord_q1 = f_chord_q2 = f_chord_q3 = 0.0
            f_chord_pattern_count = 0.0
            f_chord_pattern_count_per_simul = 0.0

            f_oscillation = 0.0
            f_heavy_hit_jitter = 0.0
            f_shape_flux = 0.0

        if f_simul_count == 0:
            f_movement = 0.0
            f_movement_interaction = 0.0
            f_movement_stayed = 0.0
            f_stayed_count = 0.0
            f_movement_lag2 = 0.0
            f_movement_lag3 = 0.0
            f_movement_lag4 = 0.0
        else:
            f_movement = from_cum("movement_cum") / f_simul_count
            f_movement_interaction = from_cum("movement_interaction_cum") / f_simul_count
            f_movement_stayed = from_cum("movement_stayed_cum") / f_simul_count
            f_stayed_count = from_cum("stayed_count_cum") / f_simul_count
            f_movement_lag2 = from_cum("movement_lag2_cum") / f_simul_count
            f_movement_lag3 = from_cum("movement_lag3_cum") / f_simul_count
            f_movement_lag4 = from_cum("movement_lag4_cum") / f_simul_count

        lane_jack = from_cum("lane_jack_cum")
        f_jack = np.sum(lane_jack)
        f_wide_2_jack = np.sum(mm["lane_jack_cum"][idx_s] - mm["lane_jack_cum"][idx_w2_s])

        active_jacks = lane_jack[lane_jack > 0]
        if active_jacks.size > 0:
            f_jack_mean = np.mean(active_jacks)
        else:
            f_jack_mean = 0.0

        f_jack_chord_conflict_delay = from_cum("jack_chord_conflict_delay_cum")

        jack_event_count = from_cum("is_jack_evt_cum")
        jack_chord_size_sum = from_cum("jack_chord_size_cum")
        jack_chord_size_sq_sum = from_cum("jack_chord_size_sq_cum")

        if jack_event_count > 0:
            f_chord_jack_size_mean = jack_chord_size_sum / jack_event_count
            var = max((jack_chord_size_sq_sum / jack_event_count) - (f_chord_jack_size_mean**2), 0.0)
            f_chord_jack_size_cv = np.sqrt(var) / f_chord_jack_size_mean
        else:
            f_chord_jack_size_mean = 0.0
            f_chord_jack_size_cv = 0.0

        idx_5s_s = get_idx(time_end - 5000)
        count_5s = idx_e - idx_5s_s

        f_ratio_mean = 0.0
        f_ratio_cv = np.log1p(0.0)

        if count_5s >= 8:
            intervals_5s = mm["intervals"][idx_5s_s:idx_e]

            rhythm_base_candidates = intervals_5s[(intervals_5s > 5.0) & (intervals_5s <= BMS.TIME_LIMIT)] 

            if len(rhythm_base_candidates) >= 4:
                vals, counts = np.unique(np.round(rhythm_base_candidates), return_counts=True)
                mode_interval = vals[np.argmax(counts)]

                mode_interval = max(mode_interval, 18.0) 

                if idx_e > idx_s:
                    win_intervals = mm["intervals"][idx_s:idx_e]
                    ratios = win_intervals / mode_interval

                    valid_ratios = ratios[(ratios <= 8.0) & (ratios > 0.01)]

                    if len(valid_ratios) > 0:
                        f_ratio_mean = np.mean(valid_ratios)
                        f_ratio_cv = np.log1p(
                                np.std(valid_ratios) / (f_ratio_mean + 1e-9)
                        )

        if f_simul_count > 0:
            f_drift_velocity = from_cum("centroid_drift_cum") / f_simul_count

            movement_total = from_cum("movement_cum")
            if movement_total > 0:
                f_triangle_gap_rate = from_cum("triangle_gap_cum") / movement_total
            else:
                f_triangle_gap_rate = 0.0

            lane_counts = from_cum("lane_count_cum")
            p = lane_counts / f_simul_count
            f_radius_gyration = np.sum(2 * p * (1.0 - p))
        else:
            f_drift_velocity = 0.0
            f_triangle_gap_rate = 0.0
            f_radius_gyration = 0.0

        def calculate_stagger_stats(prefix):
            count = from_cum(f"clustered_strike_count_{prefix}_cum")
            s_sum = from_cum(f"clustered_strike_size_{prefix}_cum")
            s_sq_sum = from_cum(f"clustered_strike_sizesq_{prefix}_cum")

            if count > 0:
                mean = s_sum / count
                var = max((s_sq_sum / count) - (mean ** 2), 0)
                std = np.sqrt(var)
                cv = std / mean if mean > 0 else 0.0
                return mean, cv
            return 0.0, 0.0

        strike_count_40 = from_cum("clustered_strike_count_40_cum")
        f_strike_count_40 = f_simul_count - strike_count_40
        f_strike_jitter_40 = np.log1p(
            from_cum("clustered_strike_jitter_40_cum") / strike_count_40 if strike_count_40 > 0 else 0.0
        )
        f_stagger_mean_40, _ = calculate_stagger_stats("40")

        if strike_count_40 > 0:
            f_drift_velocity_strike_40 = from_cum("drift_strike_40_cum") / strike_count_40

            s_mov_total = from_cum("clustered_movement_40_cum")
            if s_mov_total > 0:
                f_triangle_gap_rate_strike_40 = from_cum("gap_strike_40_cum") / s_mov_total
            else:
                f_triangle_gap_rate_strike_40 = 0.0

            s_lane_counts = from_cum("lane_strike_40_cum")
            p_s = s_lane_counts / strike_count_40
            f_radius_gyration_strike_40 = np.sum(2 * p_s * (1.0 - p_s))
        else:
            f_drift_velocity_strike_40 = 0.0
            f_triangle_gap_rate_strike_40 = 0.0
            f_radius_gyration_strike_40 = 0.0


        f_stream_active = from_cum("stream_active_cum")
        f_stream_strokes = from_cum("stream_strokes_cum")
        f_stream_crossings = from_cum("stream_crossings_cum")

        if f_strike_count_40 > 0:
            f_stream_active_mean = f_stream_active / f_strike_count_40
            f_stream_strokes_rate = f_stream_strokes / f_strike_count_40
            f_stream_crossings_rate = f_stream_crossings / f_strike_count_40
        else:
            f_stream_active_mean = 0.0
            f_stream_strokes_rate = 0.0
            f_stream_crossings_rate = 0.0




        return np.array([
            f_button_count,
            f_scratch_count,
            f_recovery,
            f_button_scratch_interaction,
            f_lane_bias_entropy,
            f_rhythm_jitter,
            f_delay_persistence,
            f_simul_count,
            f_chord_mean,
            f_chord_size_delta,
            f_chord_q1,
            f_chord_q2,
            f_chord_q3,
            f_chord_pattern_count_per_simul,
            f_oscillation,
            f_heavy_hit_jitter,
            f_shape_flux,
            f_movement,
            f_movement_interaction,
            f_movement_stayed,
            f_stayed_count,
            f_movement_lag2,
            f_movement_lag3,
            f_movement_lag4,
            f_jack,
            f_jack_mean,
            f_jack_chord_conflict_delay,
            f_chord_jack_size_mean,
            f_chord_jack_size_cv,
            f_ratio_mean,
            f_ratio_cv,

            f_drift_velocity,
            f_triangle_gap_rate,
            f_radius_gyration,

            f_strike_count_40,
            f_strike_jitter_40,
            f_stagger_mean_40,
            f_drift_velocity_strike_40,
            f_triangle_gap_rate_strike_40,
            f_radius_gyration_strike_40,

            f_stream_active,
            f_stream_strokes,
            f_stream_crossings,
            f_stream_active_mean,
            f_stream_strokes_rate,
            f_stream_crossings_rate,

        ], dtype=np.float32)
        `);
    }

    /**
     * timeline_master と song_info をセットして解析を開始
     */
    loadBMS(timeline_master, song_info) {
        const BMSClass = this.pyodide.globals.get('BMS');
        this.pythonAnalyzer = BMSClass(timeline_master, song_info);
    }

    /**
     * 特定区間のメタデータを取得し、JSの配列として返す
     */
    getWindowMeta(startMs, endMs) {
        if (!this.pythonAnalyzer) throw new Error("BMSデータがロードされていません");
        
        const pyProxy = this.pythonAnalyzer.get_window_meta(startMs, endMs);
        const result = pyProxy.toJs();
        pyProxy.destroy(); // メモリ解放
        return result;
    }
}

import codecs
import os
import re

from typing import Union, List, Tuple, Dict
from fractions import Fraction
from math import gcd
from functools import reduce
from numbers import Real
from fractions import Fraction
# import om2bms.converter.image_resizer
from om2bms.converter.data_structures import OsuMania
from om2bms.converter.data_structures import OsuTimingPoint
from om2bms.converter.data_structures import OsuManiaNote
from om2bms.converter.data_structures import OsuManiaLongNote
from om2bms.converter.data_structures import OsuBGSoundEvent
from om2bms.converter.data_structures import BMSMeasure
from om2bms.converter.data_structures import calculate_bpm
from om2bms.converter.osu import OsuBeatmapReader
from om2bms.converter.exceptions import OsuGameTypeException
from om2bms.converter.exceptions import OsuParseException
from om2bms.converter.exceptions import BMSMaxMeasuresException
from om2bms.converter.image_resizer import build_banner_name


class OsuManiaToBMSParser:
    """
    in_file: path to osu file to convert
    out_dir: directory to output the converted bms file
    filename: the name to print to console when converting
    """
    _ms_to_inverse_note_values = {}
    _mania_note_to_channel = {
        0: 16,
        1: 11,
        2: 12,
        3: 13,
        4: 14,
        5: 15,
        6: 18,
        7: 19
    }
    _mania_ln_to_channel = {
        0: 56,
        1: 51,
        2: 52,
        3: 53,
        4: 54,
        5: 55,
        6: 58,
        7: 59
    }
    _convertion_options = {}
    _out_file = None

    @staticmethod
    def _safe_text(*values, default="") -> str:
        """
        Returns the first non-empty text value, or a default string.
        """
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return default
    @staticmethod
    def normalize_bpm(self, bpm):
        """
        Return int if bpm is effectively integer, otherwise return float.
        """
        if bpm is None:
            return None
        if isinstance(bpm, Fraction):
            bpm = float(bpm)
        elif isinstance(bpm, str):
            bpm = float(bpm.strip())
        elif not isinstance(bpm, Real):
            return bpm
        bpm = float(bpm)
        if bpm <= 0:
            raise ValueError(f"Invalid BPM: {bpm}")
        bpm = round(bpm, 3)
        if abs(bpm - round(bpm)) < 1e-3:
            return int(round(bpm))
        return bpm

    def __init__(self, in_file, out_dir, filename):
        self.reset()
        self.bg_filename = None
        self.output_path = None
        self.failed = False
        self.note_count = 0
        self.ln_count = 0

        try:
            self.beatmap = OsuBeatmapReader(in_file)
        except OsuGameTypeException:
            self.failed = True
            return
        except OsuParseException as e:
            self.failed = True
            print(e)
            return
        print("\tConverting " + filename)

        self.beatmap = self.beatmap.get_parsed_beatmap()

        title_for_filename = self._safe_text(
            self.beatmap.title, self.beatmap.title_unicode, default="Untitled"
        )
        version_text = self._safe_text(
            self.beatmap.version, default="No Difficulty")
        bms_filename = title_for_filename + "[" + version_text + "]" + ".bms"
        bms_filename = re.sub('[\\/:"*?<>|]+', "", bms_filename)
        output = os.path.join(out_dir, bms_filename)
        self.output_path = output
        OsuManiaToBMSParser._out_file = codecs.open(
            output, "w", "shiftjis", errors="replace")

        self.write_buffer(self.create_header())
        music_start_param = self.music_start_time(self.beatmap)
        self.get_next_measure(
            music_start_param[0], music_start_param[1], self.beatmap)

        OsuManiaToBMSParser._out_file.close()
        file = os.path.dirname(in_file)
        if OsuManiaToBMSParser._convertion_options["BG"] and self.beatmap.stagebg is not None and \
                os.path.isfile(os.path.join(file, self.beatmap.stagebg)):
            # om2bms.converter.image_resizer.black_background_thumbnail(os.path.join(file, self.beatmap.stagebg))
            self.bg_filename = os.path.join(file, self.beatmap.stagebg)

    def get_bg(self):
        """
        Returns bg filename
        """
        return self.bg_filename

    def reset(self):
        """
        Resets class variables
        """
        OsuManiaToBMSParser._out_file = None
        OsuBeatmapReader._latest_tp_index = 0
        OsuBeatmapReader._latest_noninherited_tp_index = 0
        OsuBeatmapReader._sample_index = 1
        BMSMeasure._hit_sounds = OsuManiaToBMSParser._convertion_options["HITSOUND"]

    def write_buffer(self, buffer: Union[BMSMeasure, List[str]]):
        """
        Writes to file. \n between each element in buffer if list
        """
        if buffer is None:
            return

        elif isinstance(buffer, BMSMeasure):
            wrote_any = False

            for line in buffer.lines:
                if line is None:
                    continue

                # 防止空 dict 被写进 BMS
                if isinstance(line, dict) and not line:
                    continue

                text = str(line)

                # 防止 __str__ 后变成 "{}"
                if text.strip() == "{}":
                    continue

                if text.strip() == "":
                    continue

                OsuManiaToBMSParser._out_file.write(text)
                wrote_any = True

            if wrote_any:
                OsuManiaToBMSParser._out_file.write("\n")

        else:
            wrote_any = False

            for line in buffer:
                if line is None:
                    continue

                if isinstance(line, dict) and not line:
                    continue

                text = str(line)

                if text.strip() == "{}":
                    continue

                if text.strip() == "":
                    continue

                OsuManiaToBMSParser._out_file.write(text)
                OsuManiaToBMSParser._out_file.write("\n")
                wrote_any = True

            if wrote_any:
                OsuManiaToBMSParser._out_file.write("\n")


    def expansion_wrapper(self, n, ms_per_measure) -> Fraction:
        """
        Approximates n, where 0 < n < 1, to p/q where q=2^i or 3 * 2^i up to q=192.
        """
        def expander(n, ms_per_measure, meter, end):
            """
            Generalized expander for non upper number of 3 or 4 measures.
            """
            def within_offset(num, sum__, offset):
                """
                return true if within
                """
                return int(ms_per_measure * num) - 1 < ms_per_measure * (sum__ + offset) < int(ms_per_measure * num) + 2
            done = False
            denominator = meter
            sum_ = Fraction(1, meter)
            while sum_ + Fraction(1, denominator) < n:
                sum_ += Fraction(1, denominator)
            iterations = 0
            while iterations < 6:
                if within_offset(n, sum_, 0) or round(n, 5) == round(float(sum_), 5):
                    done = True
                    break
                if float(sum_) > n:
                    sum_ -= Fraction(1, denominator)
                    denominator *= 2
                elif float(sum_) < n:
                    sum_ += Fraction(1, denominator)
                    denominator *= 2
                iterations += 1
            # pad with maxs
            while not done:
                if within_offset(n, sum_, 0):
                    break
                prev_error = abs(n - sum_)
                if sum_ > n:
                    if within_offset(n, sum_, -Fraction(1, end)):
                        sum_ -= Fraction(1, end)
                        break
                    elif abs(n - (prev_error - Fraction(1, end))) > sum_ - Fraction(1, end):
                        break
                    sum_ -= Fraction(1, end)
                elif sum_ < n:
                    if within_offset(n, sum_, Fraction(1, end)):
                        sum_ += Fraction(1, end)
                        break
                    elif abs(n - (prev_error + Fraction(1, end))) < sum_ - Fraction(1, end):
                        break
                    sum_ += Fraction(1, end)
            return (abs(n - sum_), sum_)

        error2 = expander(n, ms_per_measure, 4, 128)
        error3 = expander(n, ms_per_measure, 3, 192)
        error = error2 if error2[0] < error3[0] else error3
        time_value = error[1]
        if time_value == 1:
            return Fraction(0, 1)
        if time_value != 0:
            self.add_to_mtnv(time_value * ms_per_measure, time_value)
        return error[1]

    def music_start_time(self, beatmap: OsuMania):
        """
        Returns the measure offset and ms of first measure.
        Calls BMSMeasure and BMSMainDataLine on the BGM start line.

        Fixed:
            - prevent create_data_line out of range like location=97 bits=96
            - normalize BGM start offset into correct measure
        """

        first_object = beatmap.objects[0]
        first_timing = beatmap.timing_points[0]

        ms_per_measure = first_timing.meter * first_timing.ms_per_beat

        use_obj = False

        if first_object.time < first_timing.time:
            start_time = first_object.time
            use_obj = True
        else:
            start_time = first_timing.time

        # find first obj on/after first timing point
        if use_obj:
            i = 0
            while i < len(beatmap.objects) and beatmap.objects[i].time < first_timing.time:
                i += 1

            if i < len(beatmap.objects):
                start_time = beatmap.objects[i].time
            else:
                start_time = first_object.time

        # normalize start_time into one measure range
        while start_time - ms_per_measure > 0:
            start_time -= ms_per_measure

        # start_time_offset is the time from 0 ms to the BGM start position in BMS
        if start_time > 0:
            start_time_offset = ms_per_measure - start_time
        else:
            start_time_offset = abs(start_time)

        start_time_offset += OsuManiaToBMSParser._convertion_options["OFFSET"]

        # 原来的写法：
        # mus_start_at_001 = True if first_object.time + ms_per_measure < ms_per_measure else False
        #
        # 等价于：
        # first_object.time < 0
        #
        # 所以这里直接写清楚。
        mus_start_at_001 = first_object.time < 0

        # ------------------------------------------------------------------
        # Important fix:
        #
        # start_time_offset 可能大于一个小节。
        # 例如原来会产生 97/96，这说明它已经超出当前小节。
        #
        # 所以这里拆成：
        #   extra_measure_from_offset = 要额外移动几个小节
        #   local_start_time_offset   = 当前目标小节内的位置
        # ------------------------------------------------------------------
        extra_measure_from_offset, local_start_time_offset = divmod(
            start_time_offset,
            ms_per_measure
        )

        extra_measure_from_offset = int(extra_measure_from_offset)

        # 浮点误差修正
        eps = 1e-7

        if abs(local_start_time_offset - ms_per_measure) < eps:
            extra_measure_from_offset += 1
            local_start_time_offset = 0

        if abs(local_start_time_offset) < eps:
            local_start_time_offset = 0

        sto_fraction = local_start_time_offset / ms_per_measure
        time_value_ratio = self.expansion_wrapper(sto_fraction, ms_per_measure)

        # ------------------------------------------------------------------
        # Decide base BMS measure
        # ------------------------------------------------------------------
        if mus_start_at_001:
            base_measure_number = 1
            measure_start = 1
        else:
            base_measure_number = 0

            if extra_measure_from_offset == 0 and time_value_ratio == 0:
                measure_start = 0
            else:
                measure_start = 1

        target_measure_number = base_measure_number + extra_measure_from_offset

        if target_measure_number < 0:
            print(
                "[music_start_time] warning: target_measure_number < 0, clamp to 000:",
                f"target_measure_number={target_measure_number}",
                f"base_measure_number={base_measure_number}",
                f"extra_measure_from_offset={extra_measure_from_offset}",
                f"start_time_offset={start_time_offset}",
                f"ms_per_measure={ms_per_measure}"
            )
            target_measure_number = 0

        bms_measure = BMSMeasure(str(target_measure_number).zfill(3))

        # ------------------------------------------------------------------
        # Final safety:
        # expansion_wrapper should now produce 0 <= numerator < denominator.
        # But keep protection here in case Fraction/rounding returns exactly 1.
        # ------------------------------------------------------------------
        loc = time_value_ratio.numerator
        bits = time_value_ratio.denominator

        if bits <= 0:
            raise ValueError(f"[music_start_time] invalid bits: {bits}")

        if loc < 0 or loc >= bits:
            measure_add, loc = divmod(loc, bits)
            target_measure_number += measure_add

            if target_measure_number < 0:
                print(
                    "[music_start_time] warning: normalized target_measure_number < 0, clamp to 000:",
                    f"target_measure_number={target_measure_number}",
                    f"measure_add={measure_add}",
                    f"loc={loc}",
                    f"bits={bits}"
                )
                target_measure_number = 0
                loc = 0

            bms_measure = BMSMeasure(str(target_measure_number).zfill(3))
        # print(
        #     "[music_start_time debug]",
        #     f"first_object.time={first_object.time}",
        #     f"first_timing.time={first_timing.time}",
        #     f"ms_per_measure={ms_per_measure}",
        #     f"start_time={start_time}",
        #     f"start_time_offset={start_time_offset}",
        #     f"local_start_time_offset={local_start_time_offset}",
        #     f"extra_measure_from_offset={extra_measure_from_offset}",
        #     f"sto_fraction={sto_fraction}",
        #     f"time_value_ratio={time_value_ratio}",
        #     f"target_measure={str(target_measure_number).zfill(3)}",
        #     f"bits={bits}",
        #     f"loc={loc}",
        #     f"mus_start_at_001={mus_start_at_001}",
        #     f"measure_start={measure_start}"
        # )
        bms_measure.create_data_line("01", bits, [
            (loc, "01")
        ])

        measure_offset = measure_start

        if beatmap.objects[0].time > start_time:
            while not start_time >= beatmap.objects[0].time:
                start_time += ms_per_measure
                measure_offset += 1
        else:
            measure_offset = 0 if measure_start == 0 else 1

        # ------------------------------------------------------------------
        # Measure length change support
        # ------------------------------------------------------------------
        if first_timing.meter != 4:
            if bms_measure.measure_number == "000":
                bms_measure.create_measure_length_change(
                    first_timing.meter / 4
                )
            else:
                bms_measure0 = BMSMeasure("000")
                bms_measure0.create_measure_length_change(
                    first_timing.meter / 4
                )
                self.write_buffer(bms_measure0)

                bms_measure.create_measure_length_change(
                    first_timing.meter / 4
                )

        self.write_buffer(bms_measure)

        if first_timing.meter != 4:
            for i in range(1, measure_offset):
                bms_measure = BMSMeasure(str(i).zfill(3))
                bms_measure.create_measure_length_change(
                    first_timing.meter / 4
                )
                self.write_buffer(bms_measure)

        first_measure_time = int(start_time)

        if first_object.time < first_measure_time - 1:
            measure_offset -= 1
            first_measure_time -= ms_per_measure

        if time_value_ratio == 0 and not mus_start_at_001:
            nearest_measure_offset = round(start_time / ms_per_measure)
            snapped_time = nearest_measure_offset * ms_per_measure
            diff = start_time - snapped_time

            if abs(diff) <= 5:
                print(
                    f"[music_start_time] auto snap within 5ms: "
                    f"start_time={start_time} -> snapped_time={snapped_time}, diff={diff}ms"
                )
                measure_offset = nearest_measure_offset
            else:
                print(
                    f"[music_start_time] warning: start_time is too far from measure boundary, "
                    f"start_time={start_time}, snapped_time={snapped_time}, diff={diff}ms, "
                    f"continue without interrupt"
                )
                measure_offset = nearest_measure_offset

        self.initialize_mtnv()

        return (measure_offset, first_measure_time)


    def get_next_measure(self, starting_measure: int, starting_ms: int, beatmap: OsuMania):
        """
        Retrieves information for each measure.
        """
        def add_to_measure(current_measure_, hitobjj_):
            """
            Adds hitobj to measure
            """
            if isinstance(hitobjj_, OsuManiaNote):
                self.note_count += 1
                column = hitobjj_.mania_column
                bmscolumn = OsuManiaToBMSParser._mania_note_to_channel[column]
                if bmscolumn not in current_measure_:
                    current_measure_[
                        OsuManiaToBMSParser._mania_note_to_channel[column]] = [hitobjj_]
                else:
                    current_measure_[
                        OsuManiaToBMSParser._mania_note_to_channel[column]].append(hitobjj_)
            elif isinstance(hitobjj_, OsuManiaLongNote):
                self.ln_count += 1
                column = hitobjj_.mania_column
                bmscolumn = OsuManiaToBMSParser._mania_ln_to_channel[column]
                if bmscolumn not in current_measure_:
                    current_measure_[
                        OsuManiaToBMSParser._mania_ln_to_channel[column]] = [hitobjj_]
                else:
                    current_measure_[
                        OsuManiaToBMSParser._mania_ln_to_channel[column]].append(hitobjj_)
            elif isinstance(hitobjj_, OsuBGSoundEvent):
                column = 1
                if column not in current_measure_:
                    current_measure_[column] = [hitobjj_]
                else:
                    current_measure_[column].append(hitobjj_)
            elif isinstance(hitobjj_, OsuTimingPoint):
                if 0 not in current_measure_:
                    current_measure_[0] = [hitobjj_]
                else:
                    current_measure_[0] = [hitobjj_]

        current_measure = {}

        current_time_in_ms = starting_ms
        truncate_measure = False
        first_timing = beatmap.noninherited_tp[0]
        ms_per_measure = first_timing.ms_per_beat * first_timing.meter
        measure_number = starting_measure
        most_recent_tp = first_timing
        i = 0
        while i < len(beatmap.objects):
            hitobj = beatmap.objects[i]

            if hitobj.time < int(current_time_in_ms + ms_per_measure) - 1 and not truncate_measure:
                if not isinstance(hitobj, OsuTimingPoint):
                    add_to_measure(current_measure, hitobj)
                elif isinstance(hitobj, OsuTimingPoint) and self.within_2_ms(current_time_in_ms, hitobj.time):
                    most_recent_tp = hitobj
                    ms_per_measure = hitobj.ms_per_beat * hitobj.meter
                    current_time_in_ms = hitobj.time
                    add_to_measure(current_measure, hitobj)
                else:
                    truncate_measure = True
            else:  # hitobj.starttime >= current_time_in_ms + ms_per_measure:
                if truncate_measure:
                    if hitobj.time - current_time_in_ms < 0:
                        truncation_frac = (
                            hitobj.time - (current_time_in_ms - ms_per_measure)) / ms_per_measure
                    else:
                        truncation_frac = (
                            hitobj.time - current_time_in_ms) / ms_per_measure
                    truncation_float = float(self.expansion_wrapper(
                        truncation_frac, ms_per_measure))
                    bmsmeasure = self.create_measure(current_measure, most_recent_tp, current_time_in_ms,
                                                     str(measure_number).zfill(
                                                         3),
                                                     truncation_float)
                    truncate_measure = False
                    self.write_buffer(bmsmeasure)
                    self.initialize_mtnv()
                    measure_number += 1
                    most_recent_tp = hitobj
                    ms_per_measure = hitobj.ms_per_beat * hitobj.meter
                    current_time_in_ms = hitobj.time
                    current_measure = {}
                    add_to_measure(current_measure, hitobj)

                    i += 1
                    continue
                else:
                    bmsmeasure = self.create_measure(current_measure, most_recent_tp, current_time_in_ms,
                                                     str(measure_number).zfill(3), 0)

                self.write_buffer(bmsmeasure)

                # move to next measure with hitnotes
                while not self.within_2_ms(current_time_in_ms, hitobj.time):
                    measure_number += 1
                    current_time_in_ms += ms_per_measure

                    if hitobj.time < int(current_time_in_ms + ms_per_measure) - 1:
                        if isinstance(hitobj, OsuTimingPoint) and not self.within_2_ms(current_time_in_ms, hitobj.time):
                            truncate_measure = True
                        elif isinstance(hitobj, OsuTimingPoint) and self.within_2_ms(current_time_in_ms, hitobj.time):
                            most_recent_tp = hitobj
                            self.initialize_mtnv()
                            ms_per_measure = hitobj.ms_per_beat * hitobj.meter
                            current_time_in_ms = hitobj.time
                        current_measure = {}
                        add_to_measure(current_measure, hitobj)
                        break

                if measure_number > 999:
                    raise BMSMaxMeasuresException("Exceeded 999 measures")

            if not truncate_measure:
                i += 1

        # for the last measure (outside loop)
        bmsmeasure = self.create_measure(current_measure, most_recent_tp, current_time_in_ms,
                                         str(measure_number).zfill(3), 0)
        self.write_buffer(bmsmeasure)

    def initialize_mtnv(self) -> None:
        """
        Reset _ms_to_inverse_note_values (bpm changes)
        """
        OsuManiaToBMSParser._ms_to_inverse_note_values = {}

    def add_to_mtnv(self, key: int, value: Fraction):
        """
        Wrapper to add into mtnv
        """
        OsuManiaToBMSParser._ms_to_inverse_note_values[key] = value
        OsuManiaToBMSParser._ms_to_inverse_note_values[key - 1] = value
        OsuManiaToBMSParser._ms_to_inverse_note_values[key + 1] = value

    def within_2_ms(self, base, n) -> bool:
        """
        True if n is close enough to base
        """
        return base - 2 <= n <= base + 2

    def create_measure(self, current_measure, timing_point: OsuTimingPoint, measure_start: float,
                    measure_number: str, measure_truncation: float):
        """
        Creates a BMSMeasure containing linedata
        Returns:
            (bms_measure, overflow_notes)
        overflow_notes format:
            { key: [note, note, ...], ... }
        """
        def get_numerator_with_gcd(fraction, gcd_) -> int:
            if fraction[1] == gcd_:
                return fraction[0]
            elif fraction[1] < gcd_:
                fraction[0] *= 2
                fraction[1] *= 2
                return get_numerator_with_gcd(fraction, gcd_)
            else:
                if fraction[1] % 3 == 0:
                    fraction[0] //= 3
                    fraction[1] //= 3
                else:
                    fraction[0] //= 4
                    fraction[0] *= 3
                    fraction[1] //= 4
                    fraction[1] *= 3
                return get_numerator_with_gcd(fraction, gcd_)

        if len(current_measure) == 0:
            return None, {}

        bms_measure = BMSMeasure(measure_number)
        overflow_notes = {}

        if timing_point.meter != 4:
            bms_measure.create_measure_length_change(timing_point.meter / 4)
            self.initialize_mtnv()
        elif measure_truncation != 0:
            bms_measure.create_measure_length_change(measure_truncation)

        ms_per_measure = timing_point.meter * timing_point.ms_per_beat

        for key in sorted(current_measure.keys()):
            denoms = []
            locations = []
            locations_ = []

            for note in current_measure[key]:
                time_value_ms = round(note.time - measure_start, 5)

                if self.within_2_ms(time_value_ms, 0):
                    time_value_ratio = Fraction(0, 1)

                elif time_value_ms < 0:
                    print("[WARN] note before current measure:",
                        "measure=", measure_number,
                        "measure_start=", measure_start,
                        "note_time=", note.time,
                        "key=", key)
                    continue

                elif time_value_ms >= ms_per_measure and not self.within_2_ms(time_value_ms, ms_per_measure):
                    print("[WARN] note beyond current measure, move to next:",
                        "measure=", measure_number,
                        "measure_start=", measure_start,
                        "note_time=", note.time,
                        "offset=", time_value_ms,
                        "ms_per_measure=", ms_per_measure,
                        "key=", key)

                    if key not in overflow_notes:
                        overflow_notes[key] = []
                    overflow_notes[key].append(note)
                    continue

                elif int(time_value_ms) in OsuManiaToBMSParser._ms_to_inverse_note_values:
                    time_value_ratio = OsuManiaToBMSParser._ms_to_inverse_note_values[int(time_value_ms)]
                else:
                    time_value_ratio = self.expansion_wrapper(
                        time_value_ms / ms_per_measure, ms_per_measure)

                denoms.append(time_value_ratio.denominator)
                locations.append(
                    ([time_value_ratio.numerator, time_value_ratio.denominator], note))

            if len(locations) == 0:
                continue

            if key == 0 and not current_measure[key][0].inherited:
                new_bpm = calculate_bpm(current_measure[key][0])
                if isinstance(new_bpm, int) and 1 <= new_bpm <= 255:
                    bms_measure.create_bpm_change_line(new_bpm)
                else:
                    bms_measure.create_bpm_extended_change_line(
                        new_bpm, self.beatmap.float_bpm)

            elif key == 1:
                locations_ = sorted(locations, key=lambda x: x[0])
                for i in range(len(locations_)):
                    num = locations_[i][0][0]
                    den = locations_[i][0][1]
                    # print("[DEBUG key=1]", measure_number, locations)
                    if num < 0 or num >= den:
                        print("[WARN] invalid BGM location:",
                            "measure=", measure_number,
                            "location=", (num, den),
                            "note_time=", getattr(locations_[i][1], "time", None))
                        continue

                    bms_measure.create_data_line(
                        str(key).zfill(2),
                        den,
                        [(num, locations_[i][1])]
                    )
            else:
                gcd_ = reduce(lambda a, b: a * b // gcd(a, b), denoms)
                for list_ in locations:
                    num = get_numerator_with_gcd(list_[0], gcd_)

                    if num < 0 or num >= gcd_:
                        print("[WARN] invalid key location:",
                            "measure=", measure_number,
                            "location=", (num, gcd_),
                            "note_time=", getattr(list_[1], "time", None),
                            "key=", key)
                        continue

                    locations_.append((num, list_[1]))

                if len(locations_) > 0:
                    bms_measure.create_data_line(
                        str(key).zfill(2),
                        gcd_,
                        sorted(locations_, key=lambda x: x[0])
                    )
        return bms_measure, overflow_notes


    def create_header(self) -> List[str]:
        """
        Makes everything before maindata field
        """
        genre_text = self._safe_text(
            self.beatmap.creator, self.beatmap.source, default="Unknown")
        title_text = self._safe_text(
            self.beatmap.title_unicode, self.beatmap.title, default="Untitled")
        version_text = self._safe_text(
            self.beatmap.version, default="No Difficulty")
        artist_text = self._safe_text(
            self.beatmap.artist_unicode, self.beatmap.artist, default="Unknown Artist")
        creator_text = self._safe_text(self.beatmap.creator)
        stage_file = self._safe_text(self.beatmap.stagebg)
        banner_file = build_banner_name(stage_file) if stage_file else ""

        # HEADER FIELD
        buffer = list([""])
        buffer.append("*---------------------- HEADER FIELD")
        buffer.append("")
        buffer.append("#PLAYER 1")
        # buffer.append("#GENRE " + genre_text)
        buffer.append("#TITLE " + title_text)
        buffer.append("#SUBTITLE " + "[" + version_text + "]")

        if creator_text:
            buffer.append(f"#ARTIST {artist_text}/obj:{creator_text}")
        else:
            buffer.append(f"#ARTIST {artist_text}")

        buffer.append(
            "#BPM " + str(int(calculate_bpm(self.beatmap.timing_points[0]))))
        buffer.append("#DIFFICULTY " + "5")
        if stage_file and OsuManiaToBMSParser._convertion_options["BG"]:
            buffer.append("#STAGEFILE " + stage_file)
            buffer.append("#BANNER " + banner_file)
        buffer.append(
            "#RANK " + str(OsuManiaToBMSParser._convertion_options["JUDGE"]))
        note_count = len(self.beatmap.hit_objects)

        total_multiplier = OsuManiaToBMSParser._convertion_options.get(
            "TOTAL_MULTIPLIER", 0.2)
        total_value = max(300, int(note_count * total_multiplier))
        buffer.append(f"#TOTAL {total_value}")


        beatmapset_id = str(self.beatmap.beatmap_set_id).strip() if self.beatmap.beatmap_set_id is not None else ""
        beatmap_id = str(self.beatmap.beatmap_id).strip() if self.beatmap.beatmap_id is not None else ""

        if self.beatmap.beatmap_set_id and self.beatmap.beatmap_id:
            buffer.append(
                f"; OSU_URL: https://osu.ppy.sh/beatmapsets/{beatmapset_id}#mania/{beatmap_id}"
                    )

        buffer.append("")
        for hs in self.beatmap.hitsound_names:
            buffer.append("#WAV" + hs[0] + " " + str(hs[1]))
        buffer.append("")
        if stage_file and OsuManiaToBMSParser._convertion_options["BG"]:
            buffer.append("#BMP01 " + stage_file)
            buffer.append("")
        if len(self.beatmap.float_bpm) > 0:
            for e in self.beatmap.float_bpm:
                buffer.append("#BPM" + str(e[0]) + " " + str(e[1]))
            buffer.append("")
        # BGM FIELD
        buffer.append("*---------------------- EXPANSION FIELD")
        buffer.append("")

        buffer.append("*---------------------- MAIN DATA FIELD")
        buffer.append("")
        buffer.append("")
        if stage_file and OsuManiaToBMSParser._convertion_options["BG"]:
            buffer.append("#00004:01")

        return buffer

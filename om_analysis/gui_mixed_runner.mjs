import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

//========== 辅助函数区==========

function log(...args) {
    console.error("[LOG]", ...args);
}

async function importByPath(absPath) {
    return await import(pathToFileURL(absPath).href);
}

function safeArray(x) {
    return Array.isArray(x) ? x : [];
}

function compactPatternCluster(cluster) {
    if (!cluster || typeof cluster !== "object") return cluster;

    const specificTypes = Array.isArray(cluster.SpecificTypes) ? cluster.SpecificTypes : [];
    const primarySpecificType = specificTypes.length > 0 ? specificTypes[0][0] : null;
    const primaryRatio = specificTypes.length > 0 ? specificTypes[0][1] : null;

    const bpm = cluster.BPM ?? null;
    const msPerBeat = bpm != null ? Math.round(60000 / bpm) : null;

    return {
        Pattern: cluster.Pattern ?? null,
        SpecificType: primarySpecificType,SpecificTypes: specificTypes,
        Mixed: cluster.Mixed ?? null,
        BPM: bpm,
        MsPerBeat: msPerBeat,
        Amount: cluster.Amount ?? null,
        Importance: cluster.Importance ?? null,
        RatingMultiplier: cluster.RatingMultiplier ?? null,
        Ratio: primaryRatio,
    };
}

function makePatternSummary(output) {
    const report = output?.report ?? null;
    const clusters = safeArray(report?.Clusters);
    const topFiveClusters = safeArray(output?.topFiveClusters);

    const byPattern = {};
    const bySpecificType = {};

    for (const c of clusters) {
        if (!c || typeof c !== "object") continue;

        const pattern = String(c.Pattern ?? c.pattern ?? "Unknown");
        const amount = Number(c.Amount ?? c.amount ?? 0);

        if (!byPattern[pattern]) {
            byPattern[pattern] = { count: 0, totalAmount: 0 };
        }
        byPattern[pattern].count +=1;
        byPattern[pattern].totalAmount += amount;

        const specificTypes = Array.isArray(c.SpecificTypes) ? c.SpecificTypes : [];

        if (specificTypes.length === 0) {
            const key = "None";
            if (!bySpecificType[key]) bySpecificType[key] = { count: 0, totalAmount: 0 };
            bySpecificType[key].count += 1;
            bySpecificType[key].totalAmount += amount;
        } else {
            for (const [typeName] of specificTypes) {
                const key = String(typeName ?? "None");
                if (!bySpecificType[key]) bySpecificType[key] = { count: 0, totalAmount: 0 };
                bySpecificType[key].count += 1;
                bySpecificType[key].totalAmount += amount;
            }
        }
    }

    return {
        hasReport: !!report,
        reportKeys: report && typeof report === "object" ? Object.keys(report) : [],
        clusterCount: clusters.length,
        topFiveClusterCount: topFiveClusters.length,
        Keys: report?.Keys ?? report?.keys ?? null,
        NoteCount: report?.NoteCount ?? report?.noteCount ?? null,
        FirstNote: report?.FirstNote ?? report?.firstNote ?? null,
        LastNote: report?.LastNote ?? report?.lastNote ?? null,
        ActiveDurationMs:
            Number.isFinite(Number(report?.LastNote ?? report?.lastNote)) &&
            Number.isFinite(Number(report?.FirstNote ?? report?.firstNote))? Number(report?.LastNote ?? report?.lastNote) -
                  Number(report?.FirstNote ?? report?.firstNote)
                : null,
        byPattern,
        bySpecificType,
    };
}

function compactResult(result) {
    if (!result) return null;
    return {
        star: result.star ?? null,
        estDiff: result.estDiff ?? null,
        numericDifficulty: result.numericDifficulty ?? null,
        numericDifficultyHint: result.numericDifficultyHint ?? null,
        lnRatio: result.lnRatio ?? null,
        columnCount: result.columnCount ?? null,
        mixedCompanellaPlan: result.mixedCompanellaPlan ?? null,};
}

function inferRoute(result) {
    const columnCount = result?.columnCount;
    const lnRatio = Number(result?.lnRatio ?? 0);
    const hint = String(result?.numericDifficultyHint ?? "").toLowerCase();

    let mode = "UNKNOWN";
    if (lnRatio <= 0.15) {
        mode = "RC";
    } else if (lnRatio >= 0.7) {
        mode = "LN";
    } else {
        mode = "MIX";
    }

    const route = [];
    route.push(".osu");
    route.push("Pattern service analyzePatternFromText");
    route.push("runMixedEstimatorFromText");
    route.push("Sunny baseline");

    if (columnCount === 4) {
        if (mode === "RC") {
            route.push("4K RC");
            route.push("优先 Azusa，失败再Daniel，最后 Sunny");if (hint.includes("azusa")) {
                route.push("实际结果倾向：Azusa");
            } else if (hint.includes("daniel")) {
                route.push("实际结果倾向：Daniel");
            } else if (hint.includes("sunny")) {
                route.push("实际结果倾向：Sunny");
            } else {
                route.push("实际结果来源：未提供 numericDifficultyHint，无法仅通过输出精确判断");
            }
        } else {
            route.push("4K Mix/LN");
            route.push("Sunny 提供 LN 部分，必要时 Daniel 或 Companella 修RC");
            if (result?.mixedCompanellaPlan) {
                route.push("检测到 mixedCompanellaPlan：需要 Companella 补RC");
            } else if (hint.includes("daniel")) {
                route.push("实际结果倾向：Daniel 修 RC + Sunny LN");
            } else {
                route.push("实际结果倾向：Sunny 为主");
            }
        }
    } else if (columnCount === 6|| columnCount === 7) {
        route.push(`${columnCount}K`);
        route.push("6K/7K 基本以 Sunny 为主");} else {
        route.push(`${columnCount ?? "未知"}K`);
        route.push("非主要支持键数，通常回退Sunny 或基础结果");
    }

    route.push("输出 star / estDiff / numericDifficulty / lnRatio / columnCount");

    return { mode, route };
}

// ========== 格式化文本摘要 ==========

function renderBar(ratio, width = 36) {
    if (ratio == null || isNaN(ratio)) return "░".repeat(width);
    const filled = Math.round(Math.max(0, Math.min(1, ratio)) * width);
    return "█".repeat(filled) + "░".repeat(width - filled);
}

function formatPatternBlock(cluster) {
    const pattern = cluster.Pattern ?? "Unknown";
    const bpm = cluster.BPM != null ? `${cluster.BPM} BPM` : "";
    const amount = cluster.Amount != null ? `${cluster.Amount.toLocaleString()} notes` : "";
    const mixed = cluster.Mixed ? "[Mixed]" : "";

    // 计算主pattern 在所有 clusters 里的相对重要度（用Importance 归一化）
    // 这里用 Ratio 作为进度条比例（主SpecificType 占比）
    const barRatio = cluster.Ratio ?? 0;
    const bar = renderBar(barRatio);

    //SpecificTypes 列表
    const specificTypes = Array.isArray(cluster.SpecificTypes) ? cluster.SpecificTypes : [];
    const typeLine = specificTypes.length > 0
        ? specificTypes
            .map(([name, ratio]) => `${name} (${(ratio * 100).toFixed(1)}%)`)
            .join(", ")
        : "-";

    return [
        `▶ ${pattern}${bpm}  ${amount}  ${mixed}`.trimEnd(),
        `    [${bar}]`,
        `    ${typeLine}`,
    ].join("\n");
}

// 在 buildSummaryText 函数里替换 pattern 合并逻辑

function buildSummaryText(compact, routeInfo, patternAnalysis) {
    const star = compact.star != null ? compact.star.toFixed(2) : "N/A";
    const estDiff = compact.estDiff ?? "N/A";
    const lnPct = compact.lnRatio != null
        ? (compact.lnRatio * 100).toFixed(1) + "%"
        : "N/A";
    const keys = compact.columnCount ?? "N/A";
    const mode = routeInfo.mode;

    const topFive = safeArray(patternAnalysis?.topFiveClusters);

    // 合并同类型 pattern
    const merged = {};
    let totalNotes = 0;

    for (const cluster of topFive) {
        const pattern = cluster.Pattern ?? "Unknown";
        const amount = cluster.Amount ?? 0;
        const bpm = cluster.BPM;
        const specificTypes = cluster.SpecificTypes ?? [];

        totalNotes += amount;

        if (!merged[pattern]) {
            merged[pattern] = {
                pattern,
                totalAmount: 0,
                bpms: new Set(),
                specificTypesMap: {},
            };
        }

        merged[pattern].totalAmount += amount;
        if (bpm) merged[pattern].bpms.add(bpm);

        for (const [typeName, ratio] of specificTypes) {
            const key = typeName ?? "Unknown";
            if (!merged[pattern].specificTypesMap[key]) {
                merged[pattern].specificTypesMap[key] = 0;
            }
            merged[pattern].specificTypesMap[key] += amount * ratio;
        }
    }

    // 按 totalAmount 降序排序
    const sortedPatterns = Object.values(merged).sort(
        (a, b) => b.totalAmount - a.totalAmount
    );

    const patternLines = sortedPatterns.map(p => {
        const bpmList = Array.from(p.bpms).sort((a, b) => b - a);
        const bpmStr = bpmList.length > 0
            ? `  ${bpmList.join("/")} BPM`
            : "";

        const amountStr = `  ${p.totalAmount.toLocaleString()} notes`;

        // 计算该 pattern 占总音符的比例
        const patternRatio = totalNotes > 0 ? p.totalAmount / totalNotes : 0;
        const bar = renderBar(patternRatio);

        // 计算 SpecificTypes 占比（基于该 pattern 内部）
        const typeEntries = Object.entries(p.specificTypesMap)
            .map(([name, weightedAmount]) => ({
                name,
                ratio: p.totalAmount > 0 ? weightedAmount / p.totalAmount : 0,
            }))
            .sort((a, b) => b.ratio - a.ratio);

        const typeLine = typeEntries.length > 0
            ? typeEntries
                .map(t => `${t.name} (${(t.ratio * 100).toFixed(1)}%)`)
                .join(", ")
            : "-";

        return [
            `▶ ${p.pattern}${bpmStr}${amountStr}`,
            `    [${bar}]`,
            `    ${typeLine}`,
        ].join("\n");
    }).join("\n\n");

    const divider = "─".repeat(52);

    return [
        divider,
        `  ★ SR: ${star}`,
        `◈ Estimate : ${estDiff}`,
        `  ♪ Mode     : ${mode}   Keys: ${keys}K   LN: ${lnPct}`,
        divider,
        "  TOP PATTERNS",
        divider,
        patternLines || "  (无Pattern数据)",
        divider,
    ].join("\n");
}


// ========== 主要逻辑函数 ==========

async function runPatternAnalysis(projectRoot, osuText, speedRate) {
    const patternServicePath = path.resolve(
        projectRoot,
        "om_analysis",
        "patterns",
        "service.js"
    );

    log("准备调用 Pattern service:", patternServicePath);

    let patternModule;
    try {
        patternModule = await importByPath(patternServicePath);
    } catch (err) {
        log("Pattern service import 失败。");
        log(err?.stack || err);
        return {
            ok: false,
            modulePath: patternServicePath,
            error: String(err?.message || err),
            stack: String(err?.stack || ""),
        };
    }

    const analyzePatternFromText =
        patternModule.analyzePatternFromText ||
        patternModule.default;

    if (typeof analyzePatternFromText !== "function") {
        return {
            ok: false,
            modulePath: patternServicePath,
            error: "service.js 没有导出 analyzePatternFromText(osuText, rate)",};
    }

    try {
        const output = await analyzePatternFromText(osuText, speedRate);
        const report = output?.report ?? null;
        const clusters = safeArray(report?.Clusters);
        const topFiveClusters = safeArray(output?.topFiveClusters);
        const summary = makePatternSummary(output);

        return {
            ok: true,
            modulePath: patternServicePath,
            rate: speedRate,
            note: "Pattern service 已调用。",
            summary,
            // stdout只输出 topFive，不输出 firstTwenty，避免 JSON 过大
            topFiveClusters: topFiveClusters.map(compactPatternCluster),
        };
    } catch (err) {
        log("analyzePatternFromText 执行失败。");
        log(err?.stack || err);
        return {
            ok: false,
            modulePath: patternServicePath,
            error: String(err?.message || err),
            stack: String(err?.stack || ""),
        };
    }
}

async function tryRunCompanella(projectRoot, osuText, options, mixedResult, mixedModule) {
    if (!mixedResult?.mixedCompanellaPlan) {
        log("没有 mixedCompanellaPlan，不调用 Companella。");
        return mixedResult;
    }

    log("检测到 mixedCompanellaPlan，准备调用 Companella修RC 部分。");

    const companellaPath = path.resolve(
        projectRoot,
        "om_analysis",
        "estimator",
        "companellaEstimator.js"
    );

    let companellaModule;
    try {
        companellaModule = await importByPath(companellaPath);
    } catch (err) {
        log("Companella import 失败，保留Mixed 原结果。");
        log(err?.stack || err);
        return mixedResult;
    }

    const runCompanella =
        companellaModule.runCompanellaEstimatorFromText ||
        companellaModule.runCampanellaEstimatorFromText ||
        companellaModule.default;

    const applyCompanellaToMixedResult = mixedModule.applyCompanellaToMixedResult;

    if (typeof runCompanella !== "function") {
        log("没有找到 Companella 入口函数，保留 Mixed 原结果。");
        return mixedResult;
    }

    if (typeof applyCompanellaToMixedResult !== "function") {
        log("没有找到 applyCompanellaToMixedResult，保留 Mixed 原结果。");
        return mixedResult;
    }

    log("正在运行 Companella...");
    const companellaResult = await runCompanella(osuText, options);
    log("Companella 结果摘要：");
    log(JSON.stringify(compactResult(companellaResult), null, 2));

    log("正在 applyCompanellaToMixedResult...");
    const finalResult = applyCompanellaToMixedResult(mixedResult, companellaResult);

    return finalResult;
}

async function main() {
    const projectRoot = process.argv[2];
    const osuPath = process.argv[3];
    const speedRateRaw = process.argv[4] ?? "1.0";
    const cvtFlagRaw = process.argv[5] ?? "";
    const withGraphRaw = process.argv[6] ?? "false";

    if (!projectRoot || !osuPath) {
        throw new Error(
            "Usage: node gui_mixed_runner.mjs <projectRoot> <osuPath> [speedRate] [cvtFlag] [withGraph]"
        );
    }

    const speedRate = Number(speedRateRaw || "1.0");
    const cvtFlag = cvtFlagRaw.trim() ? cvtFlagRaw.trim() : null;
    const withGraph = String(withGraphRaw).toLowerCase() === "true";

    log("项目根目录:", projectRoot);
    log(".osu 文件:", osuPath);
    log("speedRate:", speedRate);
    log("cvtFlag:", cvtFlag || "(空)");
    log("withGraph:", withGraph);

    const mixedPath = path.resolve(
        projectRoot,
        "om_analysis",
        "estimator",
        "mixedEstimator.js"
    );

    log("读取 .osu 文本...");
    const osuText = await readFile(osuPath, "utf-8");

    log("开始 Pattern 分析...");
    const patternAnalysis = await runPatternAnalysis(projectRoot, osuText, speedRate);

    log("Pattern 分析完成。");

    log("导入 mixedEstimator:", mixedPath);
    const mixedModule = await importByPath(mixedPath);
    const { runMixedEstimatorFromText } = mixedModule;

    if (typeof runMixedEstimatorFromText !== "function") {
        throw new Error("mixedEstimator.js 没有导出 runMixedEstimatorFromText");
    }

    const options = { speedRate, odFlag: null, cvtFlag, withGraph };

    log("开始调用 runMixedEstimatorFromText...");
    const mixedResult = await runMixedEstimatorFromText(osuText, options);
    log("runMixedEstimatorFromText 完成。");

    const finalResult = await tryRunCompanella(
        projectRoot,
        osuText,
        options,
        mixedResult,
        mixedModule
    );

    const routeInfo = inferRoute(finalResult);
    const compact = compactResult(finalResult);

    // 格式化文本摘要（给 GUI 直接展示用）
    const summaryText = buildSummaryText(compact, routeInfo, patternAnalysis);

    log("");
    log("========== 最终摘要 ==========");
    log(summaryText);

    // stdout 只输出一次、完整的 JSON，GUI 从这里解析
    console.log(JSON.stringify({
        ok: true,
        summaryText,
        route: routeInfo,
        compact,
        pattern: {
            ok: patternAnalysis.ok,
            summary: patternAnalysis.summary,
            topFiveClusters: patternAnalysis.topFiveClusters,error: patternAnalysis.error ?? null,
        },
    }));
}

main().catch((err) => {
    console.error("[ERROR]", err?.stack || err);
    console.log(JSON.stringify({
        ok: false,
        error: String(err?.message || err),stack: String(err?.stack || ""),
    }));process.exitCode = 1;
});

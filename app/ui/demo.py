from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.core.models import ValidationLevel
from app.core.product_understanding import (
    SECTION_BRAND_MEMORY,
    SECTION_DATA_GAPS,
    SECTION_HISTORY_PATTERNS,
    SECTION_PRODUCT_FACTS,
    SECTION_REVIEW_INSIGHTS,
)
from app.ui.pipeline_runner import (
    BOUNDARY_NOTICE,
    DEMO_DATA_NOTICE,
    NOT_CONNECTED_NOTICE,
    AnalysisBundle,
    PublishBundle,
    default_workspace_root,
    resolve_material_root,
    run_analysis,
    run_publish,
)

from app.demo_paths import BAG_PACK, JEANS_PACK

SECTION_LABELS = {
    SECTION_PRODUCT_FACTS: "商品参数",
    SECTION_REVIEW_INSIGHTS: "评价洞察",
    SECTION_BRAND_MEMORY: "品牌记忆",
    SECTION_HISTORY_PATTERNS: "历史重复表达",
    SECTION_DATA_GAPS: "资料缺口",
}

CHANNEL_LABELS = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "livestream": "直播",
    "visual": "视觉方案",
}

VALIDATION_STYLES = {
    ValidationLevel.PASS: ("通过", "ok"),
    ValidationLevel.WARN: ("注意", "warn"),
    ValidationLevel.FAIL: ("阻断", "fail"),
}


def render_app() -> None:
    st.set_page_config(
        page_title="AIPC 商品创意导演",
        page_icon="🧭",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_global_styles()
    _init_session_state()
    _render_sidebar()
    _render_hero()
    _render_import_panel()

    if st.session_state.analysis is None:
        _render_empty_state()
        _render_footer()
        return

    analysis: AnalysisBundle = st.session_state.analysis
    if not analysis.ok:
        st.markdown('<div class="panel panel-error">', unsafe_allow_html=True)
        st.error(analysis.message or "分析未完成，请检查素材路径。")
        _render_step_status(analysis.results)
        st.markdown("</div>", unsafe_allow_html=True)
        _render_footer()
        return

    _render_progress_bar(analysis)
    tab_understand, tab_judgment, tab_directions, tab_content, tab_export = st.tabs(
        ["商品理解", "经营判断", "创意方向", "内容生成", "导出内容包"]
    )
    with tab_understand:
        _render_understanding(analysis)
    with tab_judgment:
        _render_judgment(analysis)
    with tab_directions:
        _render_directions(analysis)
    with tab_content:
        _render_content()
    with tab_export:
        _render_export()
    _render_footer()


def _inject_global_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap');

        :root {
            --bg: #f4f7fb;
            --surface: #ffffff;
            --text: #0f172a;
            --muted: #64748b;
            --line: #e2e8f0;
            --brand: #0f766e;
            --brand-dark: #115e59;
            --accent: #f59e0b;
            --ok: #059669;
            --warn: #d97706;
            --fail: #dc2626;
            --shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
        }

        .stApp {
            background: linear-gradient(180deg, #eef6f5 0%, var(--bg) 220px, var(--bg) 100%);
            font-family: "Noto Sans SC", sans-serif;
            color: var(--text);
        }

        #MainMenu, footer, header[data-testid="stHeader"] {
            visibility: hidden;
            height: 0;
        }

        .block-container {
            padding-top: 1.2rem;
            max-width: 1180px;
        }

        .hero {
            background: linear-gradient(135deg, #0f766e 0%, #134e4a 55%, #1e293b 100%);
            border-radius: 20px;
            padding: 2rem 2.2rem;
            color: #fff;
            box-shadow: var(--shadow);
            margin-bottom: 1.2rem;
        }

        .hero h1 {
            font-size: 2rem;
            font-weight: 700;
            margin: 0 0 0.5rem 0;
            letter-spacing: -0.02em;
        }

        .hero p {
            margin: 0;
            color: rgba(255, 255, 255, 0.86);
            font-size: 1.02rem;
            line-height: 1.6;
        }

        .badge-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 1.1rem;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.35rem 0.75rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.14);
            border: 1px solid rgba(255, 255, 255, 0.22);
            font-size: 0.82rem;
            color: #f8fafc;
        }

        .panel {
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: 16px;
            padding: 1.2rem 1.3rem;
            box-shadow: 0 4px 18px rgba(15, 23, 42, 0.04);
            margin-bottom: 1rem;
        }

        .panel-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin: 0 0 0.35rem 0;
        }

        .panel-subtitle {
            color: var(--muted);
            font-size: 0.9rem;
            margin: 0 0 1rem 0;
        }

        .stepper {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.55rem;
            margin: 0.2rem 0 1.2rem 0;
        }

        .step {
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 0.75rem 0.7rem;
            text-align: center;
        }

        .step.done {
            border-color: #99f6e4;
            background: #f0fdfa;
        }

        .step.active {
            border-color: #5eead4;
            background: #ecfeff;
            box-shadow: 0 0 0 1px #5eead4 inset;
        }

        .step-num {
            width: 1.5rem;
            height: 1.5rem;
            border-radius: 999px;
            margin: 0 auto 0.35rem auto;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.78rem;
            font-weight: 700;
            background: #e2e8f0;
            color: #334155;
        }

        .step.done .step-num, .step.active .step-num {
            background: var(--brand);
            color: #fff;
        }

        .step-label {
            font-size: 0.78rem;
            color: var(--muted);
            line-height: 1.3;
        }

        .info-card {
            background: #f8fafc;
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 0.9rem 1rem;
            height: 100%;
        }

        .info-card h4 {
            margin: 0 0 0.45rem 0;
            font-size: 0.92rem;
        }

        .info-card p {
            margin: 0;
            color: var(--muted);
            font-size: 0.86rem;
            line-height: 1.55;
        }

        .tag {
            display: inline-block;
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-right: 0.35rem;
        }

        .tag.ok { background: #d1fae5; color: #065f46; }
        .tag.warn { background: #fef3c7; color: #92400e; }
        .tag.fail { background: #fee2e2; color: #991b1b; }
        .tag.neutral { background: #e2e8f0; color: #334155; }

        .direction-card {
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 1rem;
            background: #fff;
            min-height: 170px;
        }

        .direction-card.selected {
            border-color: var(--brand);
            box-shadow: 0 0 0 1px var(--brand) inset;
            background: #f0fdfa;
        }

        .direction-card h3 {
            margin: 0 0 0.45rem 0;
            font-size: 1rem;
        }

        .direction-card p {
            margin: 0;
            color: var(--muted);
            font-size: 0.86rem;
            line-height: 1.5;
        }

        .empty-state {
            text-align: center;
            padding: 2.4rem 1rem 2rem 1rem;
            color: var(--muted);
        }

        .empty-state h3 {
            color: var(--text);
            margin-bottom: 0.4rem;
        }

        .site-footer {
            margin-top: 2rem;
            padding: 1rem 0 0.5rem 0;
            border-top: 1px solid var(--line);
            color: var(--muted);
            font-size: 0.82rem;
            text-align: center;
        }

        div[data-testid="stSidebar"] {
            background: #0f172a;
            color: #e2e8f0;
        }

        div[data-testid="stSidebar"] * {
            color: #e2e8f0 !important;
        }

        div[data-testid="stSidebar"] .stButton > button {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.16);
            color: #f8fafc !important;
        }

        div[data-testid="stSidebar"] .stButton > button:hover {
            background: rgba(255, 255, 255, 0.14);
            border-color: rgba(255, 255, 255, 0.28);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.35rem;
            background: transparent;
        }

        .stTabs [data-baseweb="tab"] {
            background: #fff;
            border-radius: 10px;
            border: 1px solid var(--line);
            padding: 0.55rem 1rem;
            font-weight: 600;
        }

        .stTabs [aria-selected="true"] {
            background: #ecfeff !important;
            border-color: #5eead4 !important;
            color: var(--brand-dark) !important;
        }

        .stTextInput input, .stTextArea textarea {
            border-radius: 10px !important;
        }

        .stButton > button[kind="primary"] {
            background: var(--brand);
            border: none;
            border-radius: 10px;
            font-weight: 600;
        }

        .stButton > button[kind="primary"]:hover {
            background: var(--brand-dark);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_session_state() -> None:
    defaults = {
        "material_path": str(JEANS_PACK) if JEANS_PACK.is_dir() else "",
        "workspace_path": str(default_workspace_root()),
        "analysis": None,
        "publish": None,
        "selected_direction_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_hero() -> None:
    st.markdown(
        f"""
        <div class="hero">
            <h1>AIPC 商品创意导演</h1>
            <p>把本地商品资料变成有依据的经营判断、创意方向与可执行内容包。</p>
            <div class="badge-row">
                <span class="badge">🛡️ {BOUNDARY_NOTICE}</span>
                <span class="badge">📡 {NOT_CONNECTED_NOTICE}</span>
                <span class="badge">🧪 {DEMO_DATA_NOTICE}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### 工作台")
        st.caption("选择演示素材或填写本地路径，开始分析。")
        if JEANS_PACK.is_dir():
            if st.button("女装牛仔裤 Demo", use_container_width=True):
                st.session_state.material_path = str(JEANS_PACK)
        if BAG_PACK.is_dir():
            if st.button("包包 Demo", use_container_width=True):
                st.session_state.material_path = str(BAG_PACK)
        st.divider()
        st.markdown("**输出目录**")
        st.text_input("Workspace", key="workspace_path", label_visibility="collapsed")
        st.caption("索引、证据与导出包写入此目录，不会修改原始素材包。")


def _render_import_panel() -> None:
    st.markdown(
        """
        <div class="panel">
            <div class="panel-title">本地资料导入</div>
            <div class="panel-subtitle">选择包含经营资料、评价、图片的文件夹，系统将自动完成扫描与理解。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col_path, col_action = st.columns([4, 1])
    with col_path:
        st.text_input(
            "素材文件夹路径",
            key="material_path",
            label_visibility="collapsed",
            placeholder="例如：E:\\大二上\\天猫黑客松\\AIPC 女装牛仔裤 Demo 素材包（服装）",
            help="请选择包含 Demo经营资料.xlsx、评价、图片等的本地文件夹。",
        )
    with col_action:
        start = st.button("开始分析", type="primary", use_container_width=True)
    if start:
        root = resolve_material_root(st.session_state.material_path)
        if not root.is_dir():
            st.error(f"路径不存在：{root}")
            return
        with st.spinner("正在扫描本地资料并生成理解、判断与方向..."):
            bundle = run_analysis(root, Path(st.session_state.workspace_path))
        st.session_state.analysis = bundle
        st.session_state.publish = None
        st.session_state.selected_direction_id = None
        if bundle.ok:
            if bundle.directions and bundle.directions.directions:
                st.session_state.selected_direction_id = bundle.directions.directions[0].direction_id
            st.success("分析完成。请依次查看各模块，并选择一个创意方向。")
        else:
            st.warning(bundle.message or "分析未完成。")


def _render_empty_state() -> None:
    st.markdown(
        """
        <div class="panel empty-state">
            <h3>还没有开始分析</h3>
            <p>在上方填写素材路径，或从左侧选择 Demo 素材包，然后点击「开始分析」。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            """
            <div class="info-card">
                <h4>① 商品理解</h4>
                <p>从本地 Excel、评价与图片索引中提炼参数、洞察与资料缺口。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            """
            <div class="info-card">
                <h4>② 经营判断</h4>
                <p>基于证据给出机会、风险与下一步动作，不接入外部平台数据。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            """
            <div class="info-card">
                <h4>③ 创意与导出</h4>
                <p>生成 3 个有依据的方向，并输出小红书 / 抖音 / 直播 / 视觉内容包。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_progress_bar(analysis: AnalysisBundle) -> None:
    publish: PublishBundle | None = st.session_state.publish
    has_content = publish is not None and publish.content is not None
    has_export = publish is not None and publish.export is not None and publish.export.package_root.exists()
    steps = [
        ("1", "资料导入", "done"),
        ("2", "商品理解", "done" if analysis.understanding else "active"),
        ("3", "经营判断", "done" if analysis.judgment else ""),
        ("4", "创意方向", "done" if analysis.directions else ""),
        ("5", "内容导出", "done" if has_export else ("active" if has_content else "")),
    ]
    html = '<div class="stepper">'
    for num, label, state in steps:
        css = state or ""
        html += (
            f'<div class="step {css}">'
            f'<div class="step-num">{num}</div>'
            f'<div class="step-label">{label}</div>'
            f"</div>"
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _render_step_status(results) -> None:
    if not results:
        return
    st.markdown("#### 流水线状态")
    for result in results:
        st.write(f"- {result.step_name}: {result.status.value} {result.message or ''}")


def _render_understanding(analysis: AnalysisBundle) -> None:
    understanding = analysis.understanding
    if understanding is None:
        st.warning("暂无商品理解结果。")
        return

    st.markdown(
        f"""
        <div class="panel">
            <div class="panel-title">商品理解卡 · {understanding.product_name}</div>
            <div class="panel-subtitle">Evidence 条目 {understanding.evidence_count} 条 · 每个结论尽量绑定来源定位</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sections = [
        (SECTION_PRODUCT_FACTS, True),
        (SECTION_REVIEW_INSIGHTS, False),
        (SECTION_BRAND_MEMORY, False),
        (SECTION_HISTORY_PATTERNS, False),
        (SECTION_DATA_GAPS, False),
    ]
    for section_id, expanded in sections:
        section = understanding.section(section_id)
        if section is None or not section.facts:
            continue
        title = SECTION_LABELS[section_id]
        with st.expander(title, expanded=expanded):
            for fact in section.facts:
                st.markdown(f"**{fact.label}**")
                st.write(fact.summary)
                if fact.source_locators:
                    st.caption("来源：" + "；".join(fact.source_locators[:3]))


def _render_judgment(analysis: AnalysisBundle) -> None:
    report = analysis.judgment
    if report is None:
        st.warning("暂无经营判断结果。")
        return

    st.markdown(
        """
        <div class="panel">
            <div class="panel-title">经营判断卡</div>
            <div class="panel-subtitle">仅基于本地资料，不接入市场、利润与广告数据</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    top_left, top_right = st.columns([1, 1])
    with top_left:
        st.markdown(
            f"""
            <div class="info-card">
                <h4>推荐程度</h4>
                <p>{report.judgment.recommendation}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
            <div class="info-card" style="margin-top:0.8rem;">
                <h4>核心机会</h4>
                <p>{report.judgment.opportunity}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with top_right:
        st.markdown(
            f"""
            <div class="info-card">
                <h4>判断范围</h4>
                <p>{report.judgment_scope}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
            <div class="info-card" style="margin-top:0.8rem;">
                <h4>目标用户</h4>
                <p>{report.target_audience}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("#### 证据与风险")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**商品证据**")
        st.write(report.product_evidence)
        st.markdown("**历史风险**")
        st.write(report.history_risk)
    with c2:
        st.markdown("**用户证据**")
        st.write(report.user_evidence)
        st.markdown("**商品风险**")
        st.write(report.product_risk)

    if report.judgment.next_actions:
        st.markdown("#### 下一步动作")
        for action in report.judgment.next_actions:
            st.write(f"- {action}")


def _render_directions(analysis: AnalysisBundle) -> None:
    directions = analysis.directions
    if directions is None:
        st.warning("暂无创意方向。")
        return

    st.markdown(
        """
        <div class="panel">
            <div class="panel-title">选择创意方向</div>
            <div class="panel-subtitle">三个方向各有不同证据、风险与素材绑定，请选择一个继续生成内容</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(3)
    for index, direction in enumerate(directions.directions):
        selected = st.session_state.selected_direction_id == direction.direction_id
        card_class = "direction-card selected" if selected else "direction-card"
        with cols[index]:
            st.markdown(
                f"""
                <div class="{card_class}">
                    <h3>{direction.name}</h3>
                    <p>{direction.main_selling_point}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button(
                "选择此方向",
                key=f"pick_direction_{direction.direction_id}",
                use_container_width=True,
                type="primary" if selected else "secondary",
            ):
                st.session_state.selected_direction_id = direction.direction_id
                st.rerun()

    selected_id = st.session_state.selected_direction_id
    if not selected_id:
        return

    direction = next(item for item in directions.directions if item.direction_id == selected_id)
    st.markdown("#### 方向详情")
    detail_cols = st.columns(2)
    with detail_cols[0]:
        st.markdown(f"**目标人群**：{direction.audience}")
        st.markdown(f"**场景**：{' / '.join(direction.scenes)}")
        st.markdown(f"**平台表达**：{direction.platform_expression}")
    with detail_cols[1]:
        st.markdown(f"**避免表达**：{direction.avoid_expression}")
        if direction.risks:
            st.markdown("**风险**")
            for risk in direction.risks:
                st.write(f"- {risk}")
        if direction.open_questions:
            st.markdown("**待验证**")
            for question in direction.open_questions:
                st.write(f"- {question}")

    if st.button("生成内容并导出", type="primary"):
        with st.spinner("正在生成四类内容并导出内容包..."):
            publish = run_publish(
                analysis.context,
                selected_direction_id=selected_id,
                workspace_root=Path(st.session_state.workspace_path),
            )
        st.session_state.publish = publish
        if publish.ok:
            st.success("内容生成与导出完成。请切换到「内容生成」和「导出内容包」查看。")
        else:
            st.error(publish.message or "内容生成或导出失败。")


def _render_content() -> None:
    publish: PublishBundle | None = st.session_state.publish
    if publish is None or publish.content is None:
        st.info("请先在「创意方向」页选择方向并点击「生成内容并导出」。")
        return

    st.markdown(
        """
        <div class="panel">
            <div class="panel-title">四渠道内容</div>
            <div class="panel-subtitle">小红书 / 抖音 / 直播 / 视觉方案，附校验结果</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for item in publish.content.items:
        label = CHANNEL_LABELS.get(item.channel, item.channel)
        status_text, status_class = VALIDATION_STYLES.get(
            item.validation_level,
            (item.validation_level.value, "neutral"),
        )
        with st.expander(f"{label} · {status_text}", expanded=item.channel == "xiaohongshu"):
            st.markdown(
                f'<span class="tag {status_class}">{status_text}</span>'
                f'<span class="tag neutral">{label}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(f"### {item.title}")
            st.text(item.body)


def _render_export() -> None:
    publish: PublishBundle | None = st.session_state.publish
    if publish is None or publish.export is None:
        st.info("请先完成内容生成。")
        return

    export = publish.export
    status_class = "ok" if export.package_root.exists() else "fail"
    status_text = export.export_status

    st.markdown(
        f"""
        <div class="panel">
            <div class="panel-title">导出内容包</div>
            <div class="panel-subtitle">
                <span class="tag {status_class}">{status_text}</span>
                本地目录导出，可下载 README / manifest / 各渠道 Markdown
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if export.package_root.exists():
        st.success(f"内容包目录：`{export.package_root}`")
        dl1, dl2, dl3 = st.columns(3)
        readme = export.package_root / "README.md"
        manifest = export.package_root / "manifest.json"
        with dl1:
            if readme.is_file():
                st.download_button(
                    "下载 README.md",
                    data=readme.read_text(encoding="utf-8"),
                    file_name="README.md",
                    use_container_width=True,
                )
        with dl2:
            if manifest.is_file():
                st.download_button(
                    "下载 manifest.json",
                    data=manifest.read_text(encoding="utf-8"),
                    file_name="manifest.json",
                    use_container_width=True,
                )
        content_dir = export.package_root / "content"
        if content_dir.is_dir():
            files = sorted(content_dir.glob("*.md"))
            if files:
                st.markdown("**内容文件**")
                file_cols = st.columns(min(len(files), 4))
                for index, path in enumerate(files):
                    with file_cols[index % len(file_cols)]:
                        st.download_button(
                            f"下载 {path.name}",
                            data=path.read_text(encoding="utf-8"),
                            file_name=path.name,
                            key=f"download_{path.name}",
                            use_container_width=True,
                        )
    else:
        st.error("导出被阻断。")
        if export.blocked_reasons:
            st.write(export.blocked_reasons)
        if export.verification_errors:
            st.write(export.verification_errors)

    if publish.content is not None:
        fail_count = sum(
            1 for item in publish.content.items if item.validation_level is ValidationLevel.FAIL
        )
        warn_count = sum(
            1 for item in publish.content.items if item.validation_level is ValidationLevel.WARN
        )
        st.caption(f"内容校验：fail={fail_count} · warn={warn_count}")


def _render_footer() -> None:
    st.markdown(
        """
        <div class="site-footer">
            AIPC 商品创意导演 · 本地优先 · 证据可追溯 · 演示环境
        </div>
        """,
        unsafe_allow_html=True,
    )

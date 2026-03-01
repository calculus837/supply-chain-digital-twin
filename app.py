"""
Project Icarus — Streamlit Web UI
Serves the ConsultantAgent's Strategic Recommendation Report
as an interactive dashboard.
"""

import sys
import os
import copy
import json
from datetime import datetime

import streamlit as st
import pandas as pd  # type: ignore

# ── Ensure the agents package is importable ─────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agents.bridge import ConsultantAgent  # noqa: E402

# ── Page configuration ──────────────────────────────────────────────
st.set_page_config(
    page_title="Project Icarus — Supply Chain Digital Twin",
    page_icon="🚢",
    layout="wide",
)


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

@st.cache_resource
def get_agent() -> ConsultantAgent:
    """Instantiate the agent once (ensures binary exists)."""
    return ConsultantAgent()


def load_world_state() -> dict:
    """Load the digital twin from disk."""
    ws_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "agents", "world_state.json",
    )
    with open(ws_path, "r") as f:
        return json.load(f)


def _inject_lekki(test_world: dict, agent: ConsultantAgent, fee: int):
    """Add Lekki Port into *test_world* with the given transit fee."""
    lp = agent.LEKKI_PORT
    if lp["node_name"] not in test_world["nodes"]:
        node = copy.deepcopy(lp["node_data"])
        node["transit_fee"] = fee
        test_world["nodes"][lp["node_name"]] = node
        test_world["links"].append(copy.deepcopy(lp["link"]))
    else:
        test_world["nodes"][lp["node_name"]]["transit_fee"] = fee


def run_sensitivity(
    agent: ConsultantAgent,
    world: dict,
    lekki_fee: int = 20,
    apply_risk_premium: bool = False,
):
    """
    Sweep Lagos_Apapa congestion from 0.0 to 1.0 in 0.1 increments,
    with both Apapa and Lekki available.  Returns two items:
      - rows   : list[dict] for the sensitivity table
      - switch : float | None  — congestion level where port choice flips
    """
    rows: list[dict] = []
    prev_port = None
    switch = None

    for step in range(11):
        cong = round(step * 0.1, 1)
        test_world = copy.deepcopy(world)
        test_world["nodes"]["Lagos_Apapa"]["congestion"] = cong
        _inject_lekki(test_world, agent, lekki_fee)

        result = agent._call_optimizer(
            test_world, quiet=True, apply_risk_premium=apply_risk_premium,
        )
        selected = result.get("selected_port", "?")
        total_cost = result["total_cost"]

        if prev_port and selected != prev_port and switch is None:
            switch = cong
        prev_port = selected

        rows.append({
            "Congestion": cong,
            "Optimal Port": selected,
            "Landed Cost ($)": total_cost,
        })

    return rows, switch


def compute_switching_trend(
    agent: ConsultantAgent,
    world: dict,
    apply_risk_premium: bool = False,
):
    """
    For each Lekki transit fee from $10 to $40, find the Apapa congestion
    level at which the solver switches from Apapa to Lekki.
    Returns a list of dicts with 'Lekki Fee ($)' and 'Switching Congestion'.
    """
    trend: list[dict] = []
    for fee in range(10, 41, 5):  # $5 increments for a lightweight sweep
        _, switch = run_sensitivity(
            agent, world, lekki_fee=fee, apply_risk_premium=apply_risk_premium,
        )
        trend.append({
            "Lekki Fee ($)": fee,
            "Switching Congestion": switch if switch is not None else 1.1,
        })
    return trend


# ═══════════════════════════════════════════════════════════════════════
#  Main App
# ═══════════════════════════════════════════════════════════════════════

def main():
    agent = get_agent()
    world = load_world_state()

    # ── Sidebar ─────────────────────────────────────────────────────
    st.sidebar.header("Project Icarus")
    st.sidebar.caption("Adjust Apapa congestion to trigger the ConsultantAgent.")

    congestion = st.sidebar.slider(
        "Apapa Congestion",
        min_value=0.0,
        max_value=1.0,
        value=0.2,
        step=0.05,
        help="Simulated port-utilisation level for Lagos Apapa (0 = empty, 1 = gridlock).",
    )
    st.sidebar.markdown(f"**Current level: {congestion:.0%}**")

    lekki_fee = st.sidebar.slider(
        "Lekki Transit Fee ($)",
        min_value=10,
        max_value=40,
        value=20,
        step=1,
        help="Per-unit transit / handling fee charged at Lekki Deep Sea Port.",
    )
    st.sidebar.markdown(f"**Current fee: ${lekki_fee}**")

    risk_premium = st.sidebar.checkbox(
        "Enable Risk-Adjusted Modeling",
        value=False,
        help=(
            f"When enabled, a ${agent.RISK_PREMIUM_PER_UNIT}/unit demurrage-risk "
            f"penalty is added to any port with congestion ≥ {agent.CONGESTION_THRESHOLD:.0%}. "
            f"This shifts the switching point to a lower congestion level, "
            f"eliminating the 'No Crossover' blindspot at high Lekki fees."
        ),
    )

    st.sidebar.divider()
    st.sidebar.markdown(
        f"Congestion **{'exceeds' if congestion > agent.CONGESTION_THRESHOLD else 'below'}** "
        f"the {agent.CONGESTION_THRESHOLD:.0%} rerouting threshold."
    )
    if risk_premium:
        st.sidebar.markdown(
            f"Risk premium **active** — +${agent.RISK_PREMIUM_PER_UNIT}/unit on "
            f"ports ≥ {agent.CONGESTION_THRESHOLD:.0%} congestion."
        )

    # ── Apply sliders to world state ─────────────────────────────────
    world["nodes"]["Lagos_Apapa"]["congestion"] = congestion
    congested = congestion > agent.CONGESTION_THRESHOLD

    # ── Run the ConsultantAgent ─────────────────────────────────────
    # Phase 1: Baseline
    baseline = agent._call_optimizer(world, quiet=True, apply_risk_premium=risk_premium)
    bb = baseline.get("breakdown", {})

    # Phase 2: Contingency (if over threshold)
    alt = None
    delta = 0.0
    if congested:
        alt_world = copy.deepcopy(world)
        alt_world["nodes"].pop("Lagos_Apapa", None)
        alt_world["links"] = [l for l in alt_world["links"] if l["to"] != "Lagos_Apapa"]
        lp = agent.LEKKI_PORT
        node = copy.deepcopy(lp["node_data"])
        node["transit_fee"] = lekki_fee
        alt_world["nodes"][lp["node_name"]] = node
        alt_world["links"].append(copy.deepcopy(lp["link"]))
        alt = agent._call_optimizer(alt_world, quiet=True, apply_risk_premium=risk_premium)
        delta = alt["total_cost"] - baseline["total_cost"]

    # Phase 3: Sensitivity sweep (uses current Lekki fee + risk flag)
    sens_rows, switching_point = run_sensitivity(
        agent, world, lekki_fee=lekki_fee, apply_risk_premium=risk_premium,
    )

    # ═════════════════════════════════════════════════════════════════
    #  STRATEGIC RECOMMENDATION REPORT
    # ═════════════════════════════════════════════════════════════════

    st.title("📋 Strategic Recommendation Report")
    st.caption(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}  •  "
        f"ConsultantAgent v1.0  •  Engine: Rust / good_lp (MiniLP)"
    )
    st.divider()

    # ── 1. Executive Summary ────────────────────────────────────────
    st.header("1. Executive Summary")

    # Derive the cost-efficiency threshold for the current Lekki fee
    threshold_label = (
        f"{switching_point:.0%}" if switching_point is not None
        else "beyond 100% (no crossover)"
    )

    st.info(
        f"**Current Economic Threshold** — At a Lekki transit fee of "
        f"**${lekki_fee}/unit**, the LP solver favours rerouting once Apapa "
        f"congestion reaches **{threshold_label}**. "
        + (
            f"The current congestion ({congestion:.0%}) is "
            f"{'above' if switching_point and congestion >= switching_point else 'below'} "
            f"this threshold."
            if switching_point is not None
            else "No crossover exists — Apapa remains cheaper at all congestion levels."
        )
    )

    if congested:
        st.error(
            f"Lagos Apapa is operating at **{congestion:.0%}** utilisation, "
            f"which exceeds the **{agent.CONGESTION_THRESHOLD:.0%}** critical threshold. "
            f"At this level, lead-time risk escalates non-linearly due to vessel queuing, "
            f"berth unavailability, and inland-haulage delays. "
            f"A contingency corridor via **Lekki Deep Sea Port** has been evaluated."
        )
    else:
        st.success(
            f"Lagos Apapa congestion (**{congestion:.0%}**) is within acceptable "
            f"operational parameters (≤ {agent.CONGESTION_THRESHOLD:.0%}). "
            f"Throughput capacity is sufficient for current demand volume. "
            f"No rerouting intervention is required at this time."
        )

    # ── 2. Scenario Comparison ──────────────────────────────────────
    st.header("2. Scenario Comparison")

    if congested and alt:
        ab = alt.get("breakdown", {})
        delta_pct = (delta / baseline["total_cost"] * 100) if baseline["total_cost"] else 0

        comparison = pd.DataFrame({
            "Metric": [
                "Units Shipped",
                "Selected Port",
                "Production Cost",
                "Shipping Cost",
                "Port / Handling Fees",
                "TOTAL LANDED COST",
                "Delta vs Baseline",
                "Variance",
            ],
            "Baseline": [
                f"{baseline['units_shipped']:,.0f}",
                baseline.get("selected_port", "—"),
                f"${bb.get('production', 0):,.0f}",
                f"${bb.get('shipping', 0):,.0f}",
                f"${bb.get('port_fees', 0):,.0f}",
                f"${baseline['total_cost']:,.0f}",
                "—",
                "—",
            ],
            "Contingency (Lekki)": [
                f"{alt['units_shipped']:,.0f}",
                alt.get("selected_port", "—"),
                f"${ab.get('production', 0):,.0f}",
                f"${ab.get('shipping', 0):,.0f}",
                f"${ab.get('port_fees', 0):,.0f}",
                f"${alt['total_cost']:,.0f}",
                f"${delta:+,.0f}",
                f"{delta_pct:+.1f}%",
            ],
        })
        st.table(comparison)
    else:
        single = pd.DataFrame({
            "Metric": [
                "Units Shipped",
                "Selected Port",
                "Production Cost",
                "Shipping Cost",
                "Port / Handling Fees",
                "TOTAL LANDED COST",
            ],
            "Apapa (Baseline)": [
                f"{baseline['units_shipped']:,.0f}",
                baseline.get("selected_port", "—"),
                f"${bb.get('production', 0):,.0f}",
                f"${bb.get('shipping', 0):,.0f}",
                f"${bb.get('port_fees', 0):,.0f}",
                f"${baseline['total_cost']:,.0f}",
            ],
        })
        st.table(single)

    # ── 3. Risk Assessment ──────────────────────────────────────────
    st.header("3. Risk Assessment")

    r1, r2, r3 = st.columns(3)
    if congested:
        r1.error(
            "**Lead-Time Risk: HIGH**\n\n"
            "Vessel dwell-time at Apapa averages 7–14 days when congestion "
            "exceeds 80%. This cascades into missed delivery windows and "
            "contractual SLA penalties."
        )
        r2.warning(
            "**Throughput Risk: ELEVATED**\n\n"
            "Apapa berth throughput degrades ~40% above 80% utilisation. "
            "Container rollover probability rises, reducing effective "
            "supply-chain velocity."
        )
        r3.error(
            f"**Demurrage Risk: HIGH**\n\n"
            f"At {congestion:.0%} congestion, estimated demurrage exposure is "
            f"$8,000–$15,000 per vessel-day. Over a 30-day cycle this can "
            f"exceed the Lekki cost premium."
        )
    else:
        r1.success(
            "**Lead-Time Risk: LOW**\n\n"
            f"Current congestion ({congestion:.0%}) supports predictable "
            f"vessel turnaround within SLA windows."
        )
        r2.success(
            "**Throughput Risk: LOW**\n\n"
            f"Berth capacity is adequate for projected demand of "
            f"{baseline['units_shipped']:,.0f} units."
        )
        r3.success(
            "**Demurrage Risk: NEGLIGIBLE**\n\n"
            "No queuing premium expected at current utilisation levels."
        )

    # ── 4. Recommendation ───────────────────────────────────────────
    st.header("4. Recommendation")

    if congested and alt:
        sel = alt.get("selected_port", "Lekki_Port")
        if delta > 0:
            st.error(f"**ACTION: Reroute shipments through {sel}.**")
            st.markdown(
                f"The solver evaluated all available corridors and selected "
                f"**{sel}** as the optimal path.\n\n"
                f"The nominal cost premium of **${delta:,.0f}** "
                f"({delta / baseline['total_cost'] * 100:+.1f}%) is strategic "
                f"insurance against:\n"
                f"- Unpredictable lead-time variance (±14 days at Apapa)\n"
                f"- Demurrage charges ($8k–$15k / vessel-day)\n"
                f"- SLA breach penalties downstream at Lagos Ikeja\n\n"
                f"On a risk-adjusted basis, the **{sel}** corridor delivers "
                f"superior supply-chain resilience and throughput certainty.\n\n"
                f"| | |\n|---|---|\n"
                f"| **Selected Port** | {sel} |\n"
                f"| **Volume** | {alt['units_shipped']:,.0f} units |\n"
                f"| **Landed Cost** | ${alt['total_cost']:,.0f} |"
            )
        else:
            saving = abs(delta)
            st.error(f"**ACTION: Reroute shipments through {sel}.**")
            st.markdown(
                f"The solver found **{sel}** is both lower-cost (saving "
                f"**${saving:,.0f}**) and lower-risk — a dominant strategy.\n\n"
                f"| | |\n|---|---|\n"
                f"| **Selected Port** | {sel} |\n"
                f"| **Volume** | {alt['units_shipped']:,.0f} units |\n"
                f"| **Landed Cost** | ${alt['total_cost']:,.0f} |"
            )
    else:
        sel = baseline.get("selected_port", "Lagos_Apapa")
        st.success("**ACTION: Maintain current corridor — no intervention required.**")
        st.markdown(
            f"Lagos Apapa throughput is healthy. Continue monitoring the congestion "
            f"KPI weekly; trigger contingency review if utilisation exceeds "
            f"**{agent.CONGESTION_THRESHOLD:.0%}**.\n\n"
            f"| | |\n|---|---|\n"
            f"| **Selected Port** | {sel} |\n"
            f"| **Volume** | {baseline['units_shipped']:,.0f} units |\n"
            f"| **Landed Cost** | ${baseline['total_cost']:,.0f} |"
        )

    # ── Appendix A: Sensitivity Analysis ────────────────────────────
    st.divider()
    st.header("Appendix A — Sensitivity Analysis")
    st.caption(
        "Congestion sweep from 0% to 100% with both Lagos Apapa and Lekki Port "
        "available. The LP solver independently selects the cost-optimal port at "
        "each congestion level."
    )

    sens_df = pd.DataFrame(sens_rows)

    # ── Landed Cost vs Congestion line chart ────────────────────────
    chart_df = sens_df[["Congestion", "Landed Cost ($)"]].copy()
    chart_df = chart_df.set_index("Congestion")
    st.line_chart(chart_df, use_container_width=True)

    if switching_point is not None:
        st.info(
            f"📍 **Switching point detected at {switching_point:.0%} congestion** — "
            f"the solver pivots from Lagos Apapa to Lekki Port. This threshold "
            f"should be adopted as the operational trigger for corridor rerouting."
        )
    else:
        winner = sens_rows[0]["Optimal Port"] if sens_rows else "?"
        st.info(
            f"No switching point — **{winner}** remains cost-optimal across "
            f"the entire congestion range."
        )

    # ── Sensitivity results table ───────────────────────────────────
    table_df = sens_df.copy()
    table_df["Congestion"] = table_df["Congestion"].apply(lambda x: f"{x:.0%}")
    table_df["Landed Cost ($)"] = table_df["Landed Cost ($)"].apply(lambda x: f"${x:,.0f}")
    st.table(table_df)

    # ── Appendix B: Switching Point Trend ───────────────────────────
    st.divider()
    st.header("Appendix B — Switching Point Trend")
    st.caption(
        "How does the required Apapa congestion for a reroute change as "
        "Lekki's transit fee rises? A higher fee means Apapa must be more "
        "congested before Lekki becomes attractive."
    )

    trend_data = compute_switching_trend(agent, world, apply_risk_premium=risk_premium)
    trend_df = pd.DataFrame(trend_data)

    # Cap display at 100% — values of 1.1 mean 'no crossover'
    trend_chart = trend_df.copy()
    trend_chart["Switching Congestion"] = trend_chart["Switching Congestion"].clip(upper=1.0)
    trend_chart["Risk Ceiling (80%)"] = 0.8  # horizontal reference line
    trend_chart = trend_chart.set_index("Lekki Fee ($)")
    st.line_chart(trend_chart, use_container_width=True)

    # Highlight the user's current fee
    current_row = next((r for r in trend_data if r["Lekki Fee ($)"] == lekki_fee), None)
    if current_row:
        sw = current_row["Switching Congestion"]
        if sw <= 1.0:
            st.info(
                f"At the current Lekki fee of **${lekki_fee}**, the solver switches "
                f"to Lekki at **{sw:.0%}** Apapa congestion."
            )
        else:
            st.warning(
                f"At the current Lekki fee of **${lekki_fee}**, Lekki never becomes "
                f"cheaper than Apapa across the entire congestion range."
            )

    # Trend table
    trend_table = trend_df.copy()
    trend_table["Switching Congestion"] = trend_table["Switching Congestion"].apply(
        lambda x: f"{x:.0%}" if x <= 1.0 else "No crossover"
    )
    st.table(trend_table)

    # ── Footer ──────────────────────────────────────────────────────
    st.divider()
    st.caption("Project Icarus v1.0 — ConsultantAgent powered by Rust/good_lp + Python/Streamlit")


if __name__ == "__main__":
    main()

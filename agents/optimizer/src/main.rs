use good_lp::{constraint, default_solver, variable, Expression, SolverModel, Solution};
use serde::Deserialize;
use std::collections::HashMap;
use std::io::{self, Read};

// ── Data structures matching world_state.json ──────────────────────────

#[derive(Debug, Deserialize)]
struct WorldState {
    nodes: HashMap<String, Node>,
    links: Vec<Link>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
enum Node {
    #[serde(rename = "factory")]
    Factory { supply: f64, cost_per_unit: f64 },
    #[serde(rename = "port")]
    Port { transit_fee: f64, congestion: f64 },
    #[serde(rename = "customer")]
    Customer { demand: f64, price_per_unit: f64 },
}

#[derive(Debug, Deserialize)]
struct Link {
    from: String,
    to: String,
    #[allow(dead_code)]
    mode: String,
    #[allow(dead_code)]
    lead_time: f64,
    cost: f64,
}

/// A candidate shipping path: Factory → Port → Customer
#[derive(Debug)]
struct Path {
    port_name: String,
    production_cost: f64,
    shipping_cost: f64,
    effective_port_fee: f64, // transit_fee * (1 + congestion)
}

impl Path {
    fn cost_per_unit(&self) -> f64 {
        self.production_cost + self.shipping_cost + self.effective_port_fee
    }
}

// ── Optimiser entry point ──────────────────────────────────────────────

fn main() {
    eprintln!("Strategic Optimizer — Multi-Path Network Flow Solver");

    // ── Read JSON from stdin ───────────────────────────────────────
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("Failed to read stdin");
    let input = input.trim();
    if input.is_empty() {
        eprintln!("No input received.");
        return;
    }

    let world: WorldState = match serde_json::from_str(input) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("Invalid world_state JSON: {}", e);
            return;
        }
    };

    // ── Extract factory (source) ───────────────────────────────────
    let (factory_name, factory_supply, production_cost) = world
        .nodes
        .iter()
        .find_map(|(name, node)| match node {
            Node::Factory {
                supply,
                cost_per_unit,
            } => Some((name.clone(), *supply, *cost_per_unit)),
            _ => None,
        })
        .expect("world_state must contain a factory node");

    // ── Extract customer (sink) ────────────────────────────────────
    let (_customer_name, customer_demand) = world
        .nodes
        .iter()
        .find_map(|(name, node)| match node {
            Node::Customer { demand, .. } => Some((name.clone(), *demand)),
            _ => None,
        })
        .expect("world_state must contain a customer node");

    // ── Discover all port nodes ────────────────────────────────────
    let ports: HashMap<String, (f64, f64)> = world
        .nodes
        .iter()
        .filter_map(|(name, node)| match node {
            Node::Port {
                transit_fee,
                congestion,
            } => Some((name.clone(), (*transit_fee, *congestion))),
            _ => None,
        })
        .collect();

    // ── Build candidate paths (Factory → Port via link) ────────────
    let paths: Vec<Path> = world
        .links
        .iter()
        .filter(|l| l.from == factory_name && ports.contains_key(&l.to))
        .map(|link| {
            let (transit_fee, congestion) = ports[&link.to];
            Path {
                port_name: link.to.clone(),
                production_cost,
                shipping_cost: link.cost,
                effective_port_fee: transit_fee * (1.0 + congestion),
            }
        })
        .collect();

    if paths.is_empty() {
        eprintln!("No valid Factory → Port paths found in the network.");
        return;
    }

    // ── Print discovered paths ─────────────────────────────────────
    eprintln!("┌──────────────────────────────────────────────────────┐");
    eprintln!("│  Factory supply : {:>10.0}                         │", factory_supply);
    eprintln!("│  Customer demand: {:>10.0}                         │", customer_demand);
    eprintln!("├──────────────────────────────────────────────────────┤");
    for p in &paths {
        eprintln!(
            "│  Path via {:<16} cost/unit: {:>10.2}           │",
            p.port_name,
            p.cost_per_unit()
        );
        eprintln!(
            "│    prod={:.0}  ship={:.0}  port_fee={:.2} (fee*(1+cong))  │",
            p.production_cost, p.shipping_cost, p.effective_port_fee
        );
    }
    eprintln!("└──────────────────────────────────────────────────────┘");

    // ── Build the LP ───────────────────────────────────────────────
    //
    //  Variables:  units[i]  for each path i  (continuous, >= 0)
    //
    //  Objective:  Minimise SUM_i( units[i] * cost_per_unit[i] )
    //
    //  Constraints:
    //    SUM_i(units[i]) == customer_demand    (exactly meet demand)
    //    SUM_i(units[i]) <= factory_supply     (don't exceed supply)
    //    units[i] >= 0                         (implicit from bounds)

    let mut problem = good_lp::ProblemVariables::new();
    let unit_vars: Vec<_> = paths
        .iter()
        .map(|_| problem.add(variable().min(0.0)))
        .collect();

    // Objective: minimise total landed cost
    let objective: Expression = paths
        .iter()
        .zip(unit_vars.iter())
        .map(|(p, &v)| p.cost_per_unit() * v)
        .sum();

    // Sum of units across all paths
    let total_shipped: Expression = unit_vars.iter().copied().sum();

    let solution = problem
        .minimise(objective)
        .using(default_solver)
        .with(constraint!(total_shipped.clone() == customer_demand))
        .with(constraint!(total_shipped <= factory_supply))
        .solve()
        .expect("Solver failed — problem may be infeasible");

    // ── Extract results ────────────────────────────────────────────
    let mut results: Vec<(String, f64, f64)> = Vec::new(); // (port, units, cost)
    let mut total_cost = 0.0;
    let mut total_units = 0.0;
    let mut selected_port = String::new();
    let mut selected_units = 0.0_f64;

    for (i, p) in paths.iter().enumerate() {
        let units = solution.value(unit_vars[i]);
        let cost = units * p.cost_per_unit();
        results.push((p.port_name.clone(), units, cost));
        total_cost += cost;
        total_units += units;
        if units > selected_units {
            selected_units = units;
            selected_port = p.port_name.clone();
        }
    }

    // ── Diagnostic output on stderr ────────────────────────────────
    eprintln!();
    eprintln!("══════════ OPTIMAL SOLUTION ══════════");
    for (port, units, cost) in &results {
        if *units > 0.001 {
            eprintln!("  via {:<16} units: {:>8.2}  cost: {:>12.2}", port, units, cost);
        }
    }
    eprintln!("  ────────────────────────────────────");
    eprintln!("  Total units : {:.2}", total_units);
    eprintln!("  Total cost  : {:.2}", total_cost);
    eprintln!("══════════════════════════════════════");

    // ── Build per-path breakdown for JSON output ───────────────────
    let paths_json: Vec<serde_json::Value> = paths
        .iter()
        .enumerate()
        .map(|(i, p)| {
            let units = solution.value(unit_vars[i]);
            serde_json::json!({
                "port": p.port_name,
                "units_shipped": units,
                "cost_per_unit": p.cost_per_unit(),
                "total_path_cost": units * p.cost_per_unit(),
                "breakdown": {
                    "production": units * p.production_cost,
                    "shipping":   units * p.shipping_cost,
                    "port_fees":  units * p.effective_port_fee,
                }
            })
        })
        .collect();

    // ── Structured JSON result on stdout ───────────────────────────
    let result = serde_json::json!({
        "total_cost":    total_cost,
        "selected_port": selected_port,
        "units_shipped": total_units,
        "paths":         paths_json,
        "breakdown": {
            "production": total_units * production_cost,
            "shipping":   results.iter().map(|(_, u, _)| *u)
                            .zip(paths.iter().map(|p| p.shipping_cost))
                            .map(|(u, c)| u * c).sum::<f64>(),
            "port_fees":  results.iter().map(|(_, u, _)| *u)
                            .zip(paths.iter().map(|p| p.effective_port_fee))
                            .map(|(u, f)| u * f).sum::<f64>(),
        }
    });
    println!("{}", result);
}

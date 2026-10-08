//! Topological verification and acyclicity proofs for W1-CIP.
//!
//! Enforces Avionics Flight-Control (DO-178C Level A) style invariants:
//! Any task plan proposed by agents must be strictly acyclic before execution.

use petgraph::algo::toposort;
use petgraph::graph::{DiGraph, NodeIndex};
use std::collections::HashMap;

#[derive(Debug, PartialEq, Eq)]
pub enum DagValidationError {
    CycleDetected { cycle_summary: String },
    DanglingNodeReference { node_id: String },
}

/// A lightweight representation of a W1-CIP execution plan.
#[derive(Debug, Default)]
pub struct PlanGraph {
    nodes: Vec<String>,
    edges: Vec<(String, String)>,
}

impl PlanGraph {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add_node(&mut self, node_id: impl Into<String>) {
        self.nodes.push(node_id.into());
    }

    pub fn add_edge(&mut self, from: impl Into<String>, to: impl Into<String>) {
        self.edges.push((from.into(), to.into()));
    }

    /// Verifies that the graph is completely acyclic and returns a valid
    /// deterministic execution sequence.
    pub fn verify_acyclic(&self) -> Result<Vec<String>, DagValidationError> {
        let mut graph = DiGraph::<&str, ()>::new();
        let mut index_map: HashMap<&str, NodeIndex> = HashMap::new();

        // 1. Register all nodes
        for node in &self.nodes {
            let idx = graph.add_node(node.as_str());
            index_map.insert(node.as_str(), idx);
        }

        // 2. Register all directed edges
        for (from, to) in &self.edges {
            let from_idx = index_map.get(from.as_str()).copied().ok_or_else(|| {
                DagValidationError::DanglingNodeReference {
                    node_id: from.clone(),
                }
            })?;
            let to_idx = index_map.get(to.as_str()).copied().ok_or_else(|| {
                DagValidationError::DanglingNodeReference {
                    node_id: to.clone(),
                }
            })?;
            graph.add_edge(from_idx, to_idx, ());
        }

        // 3. Perform topological sort
        match toposort(&graph, None) {
            Ok(order) => {
                let sorted: Vec<String> = order
                    .into_iter()
                    .map(|idx| graph[idx].to_string())
                    .collect();
                Ok(sorted)
            }
            Err(cycle) => Err(DagValidationError::CycleDetected {
                cycle_summary: format!(
                    "Graph contains a cyclic dependency involving node: {}",
                    graph[cycle.node_id()]
                ),
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_acyclic_plan() {
        let mut plan = PlanGraph::new();
        plan.add_node("GoalContract");
        plan.add_node("TeamPlan");
        plan.add_node("Task1");
        plan.add_node("Evidence1");
        plan.add_edge("GoalContract", "TeamPlan");
        plan.add_edge("TeamPlan", "Task1");
        plan.add_edge("Task1", "Evidence1");

        let order = plan.verify_acyclic().expect("Plan should be acyclic");
        assert_eq!(order.len(), 4);
        assert_eq!(order[0], "GoalContract");
    }

    #[test]
    fn test_cycle_rejection() {
        let mut plan = PlanGraph::new();
        plan.add_node("A");
        plan.add_node("B");
        plan.add_edge("A", "B");
        plan.add_edge("B", "A");

        assert!(plan.verify_acyclic().is_err());
    }
}

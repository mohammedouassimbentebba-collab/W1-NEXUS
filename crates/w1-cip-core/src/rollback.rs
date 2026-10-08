//! Aerospace-grade Copy-on-Write (CoW) transactional state management for W1-CIP.
//!
//! Provides O(1) atomic rollbacks upon peer challenge failure or test regression.

use std::collections::HashMap;
use std::sync::{Arc, RwLock};

/// Immutable state branch represented by an Arc-wrapped map.
type StateBranch = Arc<HashMap<String, String>>;

/// Transactional state manager providing instant snapshotting and atomic rollback.
pub struct TransactionalStateEngine {
    current_state: Arc<RwLock<StateBranch>>,
    snapshots: Arc<RwLock<Vec<StateBranch>>>,
}

impl Default for TransactionalStateEngine {
    fn default() -> Self {
        Self::new()
    }
}

impl TransactionalStateEngine {
    pub fn new() -> Self {
        Self {
            current_state: Arc::new(RwLock::new(Arc::new(HashMap::new()))),
            snapshots: Arc::new(RwLock::new(Vec::new())),
        }
    }

    /// Captures an O(1) atomic snapshot of the current state before executing speculative changes.
    pub fn snapshot(&self) -> usize {
        let current = self.current_state.read().unwrap().clone();
        let mut snaps = self.snapshots.write().unwrap();
        snaps.push(current);
        snaps.len() - 1
    }

    /// Applies a key-value mutation by cloning the Arc pointer (Copy-on-Write).
    pub fn set(&self, key: impl Into<String>, value: impl Into<String>) {
        let mut current_guard = self.current_state.write().unwrap();
        let mut new_map = (**current_guard).clone();
        new_map.insert(key.into(), value.into());
        *current_guard = Arc::new(new_map);
    }

    /// Retrieves a value from the current state.
    pub fn get(&self, key: &str) -> Option<String> {
        let current_guard = self.current_state.read().unwrap();
        current_guard.get(key).cloned()
    }

    /// Performs an O(1) atomic rollback to the specified snapshot index.
    pub fn rollback_to(&self, snapshot_id: usize) -> bool {
        let snaps = self.snapshots.read().unwrap();
        if let Some(target_snapshot) = snaps.get(snapshot_id) {
            let mut current_guard = self.current_state.write().unwrap();
            *current_guard = target_snapshot.clone();
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_atomic_rollback_on_regression() {
        let engine = TransactionalStateEngine::new();
        engine.set("file_a.rs", "fn valid() {}");

        // 1. Take snapshot before agent edit
        let snap_id = engine.snapshot();

        // 2. Speculative agent mutation (e.g. introduces regression)
        engine.set("file_a.rs", "fn broken_syntax(");
        assert_eq!(
            engine.get("file_a.rs").unwrap(),
            "fn broken_syntax("
        );

        // 3. Challenge failed -> Execute instant O(1) rollback
        assert!(engine.rollback_to(snap_id));
        assert_eq!(
            engine.get("file_a.rs").unwrap(),
            "fn valid() {}"
        );
    }
}

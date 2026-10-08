//! BLAKE3 Merkle-DAG state ledger and cryptographic receipts for W1-CIP.
//!
//! Provides sub-microsecond content-addressing and immutable evidence receipts.

use blake3::Hasher;

/// Computes a canonical BLAKE3 cryptographic hash of a raw byte slice.
pub fn hash_bytes(data: &[u8]) -> String {
    let mut hasher = Hasher::new();
    hasher.update(data);
    hasher.finalize().to_hex().to_string()
}

/// A structured cryptographic receipt certifying that an Evidence artifact was
/// validated against a specific GoalContract hash.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct EvidenceReceipt {
    pub goal_hash: String,
    pub evidence_hash: String,
    pub combined_root: String,
    pub timestamp_epoch_ms: u64,
}

impl EvidenceReceipt {
    /// Generates a tamper-proof Merkle-pair receipt binding GoalContract to Evidence.
    pub fn create(goal_bytes: &[u8], evidence_bytes: &[u8], epoch_ms: u64) -> Self {
        let goal_hash = hash_bytes(goal_bytes);
        let evidence_hash = hash_bytes(evidence_bytes);

        // Compute Merkle parent: H(H(Goal) || H(Evidence))
        let mut parent_hasher = Hasher::new();
        parent_hasher.update(goal_hash.as_bytes());
        parent_hasher.update(evidence_hash.as_bytes());
        let combined_root = parent_hasher.finalize().to_hex().to_string();

        Self {
            goal_hash,
            evidence_hash,
            combined_root,
            timestamp_epoch_ms: epoch_ms,
        }
    }

    /// Verifies the internal cryptographic consistency of this receipt.
    pub fn verify(&self) -> bool {
        let mut parent_hasher = Hasher::new();
        parent_hasher.update(self.goal_hash.as_bytes());
        parent_hasher.update(self.evidence_hash.as_bytes());
        let expected_root = parent_hasher.finalize().to_hex().to_string();
        self.combined_root == expected_root
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_merkle_receipt_creation_and_verification() {
        let goal = b"{\"criteria\": \"Build deterministic DAG\"}";
        let evidence = b"{\"stdout\": \"Tests passed with 0 errors\"}";

        let receipt = EvidenceReceipt::create(goal, evidence, 1728345600000);
        assert!(receipt.verify());
        assert_eq!(receipt.goal_hash.len(), 64);
        assert_eq!(receipt.evidence_hash.len(), 64);
        assert_eq!(receipt.combined_root.len(), 64);
    }
}

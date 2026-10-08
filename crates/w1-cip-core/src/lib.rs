//! W1-CIP Core: Systems-Level Deterministic State Engine & Merkle-DAG Ledger.
//!
//! Provides C-ABI compatible FFI entry points for Python, Node.js, and Go bindings.

pub mod dag;
pub mod merkle;
pub mod rollback;

use std::ffi::{CStr, CString};
use std::os::raw::c_char;

/// Computes BLAKE3 hash of a null-terminated UTF-8 string.
/// Caller must free the returned string using `w1_free_string`.
#[no_mangle]
pub extern "C" fn w1_blake3_hash(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return std::ptr::null_mut();
    }
    let c_str = unsafe { CStr::from_ptr(input) };
    let bytes = c_str.to_bytes();
    let hash = merkle::hash_bytes(bytes);
    CString::new(hash).unwrap().into_raw()
}

/// Verifies DAG acyclicity given JSON array of [from, to] edges.
/// Returns 1 if acyclic (valid), 0 if cyclic or invalid JSON.
#[no_mangle]
pub extern "C" fn w1_dag_verify_acyclic_json(edges_json: *const c_char) -> i32 {
    if edges_json.is_null() {
        return 0;
    }
    let c_str = unsafe { CStr::from_ptr(edges_json) };
    let json_str = match c_str.to_str() {
        Ok(s) => s,
        Err(_) => return 0,
    };

    let edges: Vec<(String, String)> = match serde_json::from_str(json_str) {
        Ok(e) => e,
        Err(_) => return 0,
    };

    let mut plan = dag::PlanGraph::new();
    for (from, to) in edges {
        plan.add_node(from.as_str());
        plan.add_node(to.as_str());
        plan.add_edge(from, to);
    }

    if plan.verify_acyclic().is_ok() {
        1
    } else {
        0
    }
}

/// Frees a C-string allocated by Rust FFI.
#[no_mangle]
pub extern "C" fn w1_free_string(ptr: *mut c_char) {
    if !ptr.is_null() {
        unsafe {
            let _ = CString::from_raw(ptr);
        }
    }
}

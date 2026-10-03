//! Huffman trie construction from (codeword, length) pairs.
//!
//! Shared by the build script, which emits the nodes as static data, and by
//! core tests. Only `alloc` paths are used so both contexts compile it.
use alloc::{vec, vec::Vec};

/// One node: the child for bit 0, the child for bit 1, then the symbol.
/// A child of 0 means "none" because the root is never anyone's child; a
/// symbol of [`NO_SYMBOL`] marks an internal node.
pub type Node = [u16; 3];
pub const NO_SYMBOL: u16 = u16::MAX;

/// Insert every codeword MSB first. Codeword prefixes and duplicate leaves are
/// rejected exactly as the original runtime constructor rejected them.
pub fn build(codes: &[u32], bits: &[usize]) -> Vec<Node> {
    let mut nodes: Vec<Node> = vec![[0, 0, NO_SYMBOL]];
    assert_eq!(codes.len(), bits.len());
    for (symbol, (&code, &width)) in codes.iter().zip(bits).enumerate() {
        let mut node = 0;
        for shift in (0..width).rev() {
            assert!(nodes[node][2] == NO_SYMBOL);
            let bit = ((code >> shift) & 1) as usize;
            node = if nodes[node][bit] != 0 {
                usize::from(nodes[node][bit])
            } else {
                let next = nodes.len();
                nodes.push([0, 0, NO_SYMBOL]);
                nodes[node][bit] = u16::try_from(next).expect("trie node index fits u16");
                next
            };
        }
        assert!(nodes[node] == [0, 0, NO_SYMBOL]);
        let symbol = u16::try_from(symbol).expect("trie symbol fits u16");
        assert!(symbol != NO_SYMBOL);
        nodes[node][2] = symbol;
    }
    nodes
}

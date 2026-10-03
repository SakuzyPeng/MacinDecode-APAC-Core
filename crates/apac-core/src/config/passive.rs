//! Passive scene-position syntax. Coordinates remain encoded; no spatial renderer.
use super::{
    ParseError, ParseStatus,
    parser::{InBand, PResult, Parser, Stop},
};
use crate::prelude::*;
use crate::record::{DigestUnit, FieldValue};

#[derive(Clone, Debug, Default, PartialEq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
pub struct PositionSyntax {
    pub parent_dynamic: bool,
    pub range_dynamic: bool,
    pub position_precision: usize,
    pub rotation_precision: usize,
    pub parent: usize,
    pub polar: bool,
}

impl Parser<'_> {
    pub(super) fn position(
        &mut self,
        p: &str,
        shape: &mut PositionSyntax,
        full: bool,
    ) -> PResult<()> {
        if full {
            shape.parent_dynamic = self.flag(format_args!("{p}.parent_dynamic"))?;
            shape.range_dynamic = self.flag(format_args!("{p}.range_dynamic"))?;
            shape.position_precision =
                (self.take(format_args!("{p}.position_precision_encoded"), 4)? as usize * 2)
                    .min(24);
            shape.rotation_precision =
                (self.take(format_args!("{p}.rotation_precision_encoded"), 4)? as usize * 2)
                    .min(24);
        }
        if full || (shape.parent_dynamic && self.flag(format_args!("{p}.parent_update"))?) {
            shape.parent = if self.flag(format_args!("{p}.absolute_parent"))? {
                0
            } else {
                self.take(format_args!("{p}.parent_index"), 6)? as usize
            };
        }
        if full || (shape.range_dynamic && self.flag(format_args!("{p}.range_update"))?) {
            self.take(format_args!("{p}.range_encoded"), 4)?;
        }
        if self.flag(format_args!("{p}.position_present"))? {
            let delta = !full && self.flag(format_args!("{p}.position_delta"))?;
            if !delta {
                shape.polar = self.flag(format_args!("{p}.polar"))?;
            }
            let widths = if shape.polar { [7, 6, 5] } else { [6; 3] };
            for (i, base) in widths.into_iter().enumerate() {
                // The zero-precision polar radius has no delta magnitude bits;
                // the bound syntax omits even its otherwise redundant sign.
                if delta && shape.polar && shape.position_precision == 0 && i == 2 {
                    continue;
                }
                self.take(
                    format_args!("{p}.position_encoded[{i}]"),
                    base + shape.position_precision - if delta { 4 } else { 0 },
                )?;
            }
        }
        if self.flag(format_args!("{p}.rotation_present"))? {
            let delta = !full && self.flag(format_args!("{p}.rotation_delta"))?;
            for i in 0..4 {
                self.take(
                    format_args!("{p}.rotation_encoded[{i}]"),
                    8 + shape.rotation_precision - if delta { 4 } else { 0 },
                )?;
            }
        }
        if self.flag(format_args!("{p}.extensions_present"))? {
            for i in 0..32 {
                let q = format!("{p}.extensions[{i}]");
                if self.take(format_args!("{q}.type"), 4)? == 0 {
                    return Ok(());
                }
                let width = self.take(format_args!("{q}.length_width_minus_four"), 4)? as usize + 4;
                let count = self.take(format_args!("{q}.bits_minus_one"), width)? as usize + 1;
                self.passive_bits(format_args!("{q}.payload"), count)?;
            }
            return self.invalid("position-extensions", "too many position extensions");
        }
        Ok(())
    }
    pub(super) fn scene_graph(&mut self) -> PResult<()> {
        let p = "ancillary.scene_graph";
        self.begin_graph_digest();
        let count = self.drc_count(format_args!("{p}.count"), 6, 18)?;
        let mut shapes = vec![PositionSyntax::default(); count];
        for (i, shape) in shapes.iter_mut().enumerate() {
            self.position(&format!("{p}.positions[{i}]"), shape, true)?;
        }
        validate_graph(&shapes, self.pos())?;
        let trace_sha256 = self.end_graph_digest();
        self.derive(
            format_args!("{p}.syntax"),
            FieldValue::Positions(shapes.clone()),
        );
        self.config.ancillary.scene_graph = Some(super::SceneGraph {
            positions: shapes,
            trace_sha256,
        });
        Ok(())
    }
    pub(super) fn passive_bits(&mut self, p: impl core::fmt::Display, count: usize) -> PResult<()> {
        if count == 0 {
            return Ok(());
        }
        let start = self.pos();
        if count > self.bits.remaining() {
            return Err(
                ParseError::new(start, "truncated", "passive payload exceeds input").into(),
            );
        }
        let mut bytes = Vec::with_capacity(count.div_ceil(8));
        let mut left = count;
        while left != 0 {
            let width = left.min(8);
            bytes.push((self.bits.read(width)? << (8 - width)) as u8);
            left -= width;
        }
        self.record(
            p,
            start,
            FieldValue::digest(DigestUnit::Bits, count, &bytes),
        )
    }
}

pub(crate) fn validate_graph(shapes: &[PositionSyntax], at: usize) -> Result<(), ParseError> {
    for first in 0..shapes.len() {
        let mut current = first + 1;
        let mut visited = vec![false; shapes.len()];
        while current != 0 {
            if current > shapes.len() {
                return Err(ParseError::new(
                    at,
                    "scene-parent",
                    "scene parent is not declared",
                ));
            }
            if visited[current - 1] {
                return Err(ParseError::new(
                    at,
                    "scene-parent-cycle",
                    "cyclic scene position references",
                ));
            }
            visited[current - 1] = true;
            current = shapes[current - 1].parent;
        }
    }
    Ok(())
}

pub(crate) fn graph_update(
    data: &[u8],
    offset: usize,
    shapes: &mut [PositionSyntax],
    record: bool,
) -> Result<(InBand, usize), ParseError> {
    let mut parser = Parser::new(data, record);
    parser.bits.skip(offset)?;
    let mut next = shapes.to_vec();
    let result = (|| -> PResult<()> {
        for (i, shape) in next.iter_mut().enumerate() {
            let p = format!("ancillary.scene_graph.positions[{i}]");
            if parser.flag(format_args!("{p}.updated"))? {
                parser.position(&p, shape, false)?;
            }
        }
        validate_graph(&next, parser.bits.position())?;
        Ok(())
    })();
    match result {
        Ok(()) => shapes.clone_from_slice(&next),
        Err(Stop::Invalid(error)) => return Err(error),
        Err(Stop::Unsupported {
            position, reason, ..
        }) => parser.stopped(position, reason, ParseStatus::Partial),
    }
    let end = parser.bits.position();
    Ok((parser.in_band(), end))
}

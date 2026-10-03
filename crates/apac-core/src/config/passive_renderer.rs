//! Renderer-data configuration syntax. All numeric values remain encoded.
use super::{
    parser::{PResult, Parser},
    passive::PositionSyntax,
};
use crate::prelude::*;

impl Parser<'_> {
    pub(super) fn renderer_data(&mut self, p: &str) -> PResult<()> {
        let count = self.esc(format_args!("{p}.parameter_count"), [4, 7, 10])?;
        if count > 128 {
            return self.invalid(
                "renderer-parameter-count",
                "renderer data exceeds 128 parameters",
            );
        }
        for i in 0..count {
            let q = format!("{p}.parameters[{i}]");
            let kind = self.esc(format_args!("{q}.id"), [4, 7, 10])?;
            match kind {
                0 | 18 | 20 => self.position(&q, &mut PositionSyntax::default(), true)?,
                1 => {
                    if self.flag(format_args!("{q}.legacy_spread"))? {
                        let height = self.flag(format_args!("{q}.height_present"))?;
                        self.passive_values(&q, &[9, 8])?;
                        if height {
                            self.take(format_args!("{q}.height_encoded"), 6)?;
                        }
                    } else {
                        self.flag(format_args!("{q}.range_dynamic"))?;
                        let width =
                            self.take(format_args!("{q}.precision_encoded"), 4)? as usize * 2 + 6;
                        let third = self.flag(format_args!("{q}.third_dimension"))?;
                        self.take(format_args!("{q}.range_encoded"), 4)?;
                        self.passive_values(&q, &vec![width; if third { 3 } else { 2 }])?;
                    }
                }
                2 => {
                    self.take(format_args!("{q}.encoded"), 7)?;
                }
                3 => {
                    self.take(format_args!("{q}.encoded"), 8)?;
                }
                4 => {
                    if self.flag(format_args!("{q}.present"))? {
                        self.take(format_args!("{q}.encoded"), 7)?;
                    }
                }
                5 => self.passive_values(&q, &[8, 1, 8])?,
                6 | 14 | 21 => {
                    self.flag(format_args!("{q}.encoded"))?;
                }
                7 => {
                    if self.flag(format_args!("{q}.present"))?
                        && !self.flag(format_args!("{q}.all"))?
                    {
                        let cartesian = self.flag(format_args!("{q}.cartesian"))?;
                        let count = self.drc_count(format_args!("{q}.count"), 4, 5)?;
                        for j in 0..count {
                            let r = format!("{q}.regions[{j}]");
                            if self.flag(format_args!("{r}.predefined"))? {
                                self.take(format_args!("{r}.index"), 4)?;
                            } else if cartesian {
                                self.passive_values(&r, &[8; 6])?;
                            } else {
                                self.passive_values(&r, &[9, 9, 8, 8])?;
                            }
                        }
                    }
                }
                8 | 15 => {
                    self.take(format_args!("{q}.encoded"), 3)?;
                }
                9 => match self.take(format_args!("{q}.type"), 3)? {
                    0 => {}
                    1 => {
                        self.take(format_args!("{q}.index"), 10)?;
                        self.metadata_reverb(&format!("{q}.reverb"))?;
                    }
                    2 => {
                        self.metadata_resource(&format!("{q}.resource"))?;
                        self.take(format_args!("{q}.index"), 10)?;
                        self.metadata_reverb(&format!("{q}.reverb"))?;
                    }
                    3 => {
                        self.metadata_resource(&format!("{q}.resource"))?;
                        if self.take(format_args!("{q}.fallback_type"), 3)? != 0 {
                            return self.stop(
                                "reference renderer does not implement this room-geometry fallback",
                            );
                        }
                        self.passive_values(&q, &[32; 32])?;
                    }
                    _ => return self.invalid("renderer-reverb-type", "reserved scene reverb type"),
                },
                10 => {
                    if self.take(format_args!("{q}.type"), 3)? == 1
                        && self.flag(format_args!("{q}.present"))?
                    {
                        self.take(format_args!("{q}.encoded"), 7)?;
                    }
                }
                11 => self.renderer_radiation(&q)?,
                12 => {
                    self.flag(format_args!("{q}.flag"))?;
                    if self.flag(format_args!("{q}.present"))? {
                        self.take(format_args!("{q}.encoded"), 9)?;
                    }
                }
                13 => self.passive_values(&q, &[1, 1, 3])?,
                16 => {
                    self.take(format_args!("{q}.encoded"), 9)?;
                }
                17 => {
                    if self.flag(format_args!("{q}.present"))? {
                        if self.flag(format_args!("{q}.predefined"))? {
                            self.take(format_args!("{q}.index"), 4)?;
                        } else if self.flag(format_args!("{q}.cartesian"))? {
                            self.passive_values(&q, &[8; 6])?;
                        } else {
                            self.passive_values(&q, &[9, 9, 8, 8, 8, 8])?;
                        }
                    }
                }
                19 => {
                    if !self.flag(format_args!("{q}.default"))? {
                        self.take(format_args!("{q}.order"), 4)?;
                    }
                    self.take(format_args!("{q}.encoded"), 8)?;
                }
                22 => match self.take(format_args!("{q}.location"), 2)? {
                    0 | 2 => {
                        self.esc(format_args!("{q}.parameter_0"), [1, 3, 8])?;
                        self.esc(format_args!("{q}.parameter_1"), [1, 3, 8])?;
                    }
                    1 => {
                        let order = self.take(format_args!("{q}.order"), 4)? as usize;
                        let layout = self.take(format_args!("{q}.layout_tag"), 32)?;
                        let float = self.flag(format_args!("{q}.float_coefficients"))?;
                        if !float {
                            self.take(format_args!("{q}.scale_bits"), 32)?;
                        }
                        let count = (layout as usize & 65535) * (order + 1) * (order + 1);
                        self.passive_bits(
                            format_args!("{q}.coefficient_bits"),
                            count * if float { 32 } else { 16 },
                        )?;
                    }
                    _ => {
                        return self.invalid(
                            "renderer-matrix-location",
                            "reserved rendering-matrix data location",
                        );
                    }
                },
                23 => {
                    self.take(format_args!("{q}.encoded"), 16)?;
                }
                24 => {
                    if self.take(format_args!("{q}.type"), 2)? != 0
                        && self.flag(format_args!("{q}.present"))?
                    {
                        self.take(format_args!("{q}.encoded"), 16)?;
                    }
                }
                _ => {
                    let bytes =
                        self.esc(format_args!("{q}.bytes_minus_one"), [4, 8, 16])? as usize + 1;
                    self.passive_bits(format_args!("{q}.payload"), bytes * 8)?;
                }
            }
        }
        Ok(())
    }
    fn renderer_precision(&mut self, p: &str, width: usize) -> PResult<()> {
        if width == 64 {
            return self
                .stop("reference renderer does not implement double-precision radiation data");
        }
        self.take(p, width)?;
        Ok(())
    }
    fn renderer_radiation(&mut self, p: &str) -> PResult<()> {
        match self.take(format_args!("{p}.type"), 3)? {
            0 => {}
            1 => {
                self.take(format_args!("{p}.index"), 10)?;
            }
            2 => {
                self.metadata_resource(&format!("{p}.resource"))?;
                self.take(format_args!("{p}.index"), 10)?;
            }
            3 => {
                let width = [8, 16, 32, 64][self.take(format_args!("{p}.precision"), 2)? as usize];
                let kind = self.take(format_args!("{p}.pattern"), 5)?;
                match kind {
                    0 => {}
                    1 | 2 => {
                        let count = self.esc(format_args!("{p}.count_minus_one"), [3, 6, 9])? + 1;
                        let count = self.count(count, 11)?;
                        for i in 0..count {
                            let q = format!("{p}.bands[{i}]");
                            // The bound reader consumes this flag before the frequency.
                            let unity = self.flag(format_args!("{q}.unity"))?;
                            self.esc(format_args!("{q}.frequency_minus_one"), [10, 13, 17])?;
                            if !unity {
                                if kind == 1 {
                                    if self.flag(format_args!("{q}.compact"))? {
                                        self.passive_values(&q, &[3, 8])?;
                                    } else {
                                        for j in 0..2 {
                                            self.renderer_precision(
                                                &format!("{q}.encoded[{j}]"),
                                                width,
                                            )?;
                                        }
                                    }
                                } else {
                                    self.passive_values(&q, &[9, 9])?;
                                    self.renderer_precision(&format!("{q}.gain_encoded"), width)?;
                                }
                            }
                        }
                    }
                    3 => {
                        if !self.flag(format_args!("{p}.unity"))? {
                            self.renderer_precision(&format!("{p}.gain_encoded"), width)?;
                            self.take(format_args!("{p}.angle_encoded"), 9)?;
                        }
                    }
                    _ => {
                        let bytes =
                            self.esc(format_args!("{p}.bytes_minus_one"), [4, 8, 16])? as usize + 1;
                        self.passive_bits(format_args!("{p}.payload"), bytes * 8)?;
                    }
                }
            }
            4 => {
                let count = self.esc(format_args!("{p}.count_minus_one"), [3, 6, 9])? + 1;
                let count = self.count(count, 6)?;
                let mut entries = 0;
                for i in 0..count {
                    entries += self.esc(format_args!("{p}.sizes_minus_one[{i}]"), [6, 9, 12])?
                        as usize
                        + 1;
                }
                self.passive_bits(format_args!("{p}.coefficient_bits"), entries * 32)?;
            }
            _ => return self.invalid("renderer-radiation-type", "reserved radiation pattern type"),
        }
        Ok(())
    }
}

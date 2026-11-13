//! Report-level inspection: packet and frame parsers that record syntax.
//!
//! Decoding needs none of this; it is the layer `parse-packets` and the
//! `decode-sq` report are built from. Contexts are built from a [`Config`]
//! (or cookie bytes) like the decoder's, the `parse_*` functions return the
//! report types (serializable with the `serde` feature), and the
//! `*_with_state` variants carry the same per-stream state as [`Decoder`],
//! so a packet sequence can be reported exactly as it is decoded.
//!
//! [`Config`]: crate::Config
//! [`Decoder`]: crate::Decoder

pub use crate::frame::drc::{DrcState, parse_drc_with_state};
pub use crate::frame::stream::{StreamState, parse_with_state as parse_stream_packet_with_state};
pub use crate::frame::{
    AdditionalComponentConfiguration, AmbientCombination, AmbientContribution, AmbientSpectrum,
    AmbientTransform, AuxiliaryPayload, Bwe2Analysis, Bwe2ChannelData, Bwe2ChannelSpectrum,
    Bwe2Data, Bwe2Parameters, Bwe2Region, Bwe2Report, CacChannelSpectrum, CacData, CacReport,
    CacRun, ChannelFrameContext, ChannelPacketReport, ChannelPreroll, ChannelSpectrum,
    DecodedFrameContext, DrcConfiguration, DrcGainExtension, DrcGainSequence, DrcNode,
    DrcParameters, DrcPayload, DrcReport, DrcSequenceParameters, DrcTimeDelta, DynamicBandMapping,
    DynamicSelectionData, DynamicSelectionEncoding, ElementBwe2Data, ElementConfiguration,
    ElementKind, ElementReport, EmbeddedPreroll, FrameContext, FrameReport, HoaAdditiveData,
    HoaCoefficientSpectrum, HoaExtensionData, HoaFrameConfiguration, HoaFrameConfigurationReport,
    HoaFrameContext, HoaFrameInfo, HoaMixedMapping, HoaPacketReport, HoaSourceChannelSpectrum,
    HoaSourceLayoutData, HoaSpatialControls, HoaSpatialData, HoaState, HoaStaticRemapping, IcsInfo,
    InternalAmbientData, InternalAmbientSpectrum, PacketReport, PacketTail, ParseMode,
    RecoverySlotSpectrum, SalientComponentConfiguration, SalientComponentOrderInfo,
    SalientDescriptor, SalientSpatialData, SalientState, SalientSubbandInfo, ScanWorkspace,
    SceneGraphPayload, SceneGraphState, Section, SpectrumReport, StaticAmbientData,
    StreamComponentConfiguration, StreamComponentReport, StreamFrameContext, StreamOutputRange,
    StreamPacketReport, StreamPreroll, TnsChannel, TnsChannelSpectrum, TnsFilter, TnsReport,
    TnsWindow, TrimmingDeclaration, UnparsedRange, parse_bwe2, parse_cac, parse_channel_packet,
    parse_channel_packet_with_state, parse_drc, parse_frame, parse_hoa_packet,
    parse_hoa_packet_with_state, parse_packet, parse_packet_with_state, parse_spectrum,
    parse_stream_packet, parse_tns,
};
pub use crate::synthesis::MetadataState;

use super::*;
use crate::test_source::Shared;

const STEREO: u32 = (101 << 16) | 2;
const COOKIE: &[u8] = &[
    0, 0, 0, 26, 100, 97, 112, 97, 0, 0, 0, 0, 8, 0, 124, 1, 128, 4, 4, 32, 0, 18, 0, 202, 0, 0,
];
fn open(source: &Shared) -> Result<CafReader<Shared>> {
    CafReader::new(source.clone())
}
fn chunk(tag: &[u8; 4], payload: &[u8]) -> Vec<u8> {
    let mut out = tag.to_vec();
    out.extend((payload.len() as i64).to_be_bytes());
    out.extend(payload);
    out
}
fn fixture() -> Vec<u8> {
    let mut out = b"caff\0\x01\0\0".to_vec();
    let mut desc = 48000f64.to_be_bytes().to_vec();
    for n in [u32::from_be_bytes(*b"apac"), 0, 0, 1024, 2, 0] {
        desc.extend(n.to_be_bytes());
    }
    out.extend(chunk(b"desc", &desc));
    out.extend(chunk(b"kuki", COOKIE));
    let mut pakt = 2i64.to_be_bytes().to_vec();
    pakt.extend(1900i64.to_be_bytes());
    pakt.extend(100i32.to_be_bytes());
    pakt.extend(48i32.to_be_bytes());
    pakt.extend([1, 2]);
    out.extend(chunk(b"pakt", &pakt));
    out.extend(chunk(b"data", &[0, 0, 0, 7, 20, 30, 40]));
    out
}
#[test]
fn bounded_packet_iteration_and_exact_time_cropping() {
    let tmp = Shared::new(&fixture());
    let mut r = open(&tmp).unwrap();
    assert_eq!(r.track().cookie, COOKIE);
    assert_eq!(
        r.track().table,
        PacketTable {
            valid_frames: 1900,
            priming_frames: 100,
            remainder_frames: 48
        }
    );
    let packet = |index, raw_frame, bytes: &[u8]| Packet {
        index,
        raw_frame,
        bytes: bytes.to_vec(),
    };
    assert_eq!(r.next_packet().unwrap().unwrap(), packet(0, 0, &[20]));
    assert_eq!(
        r.next_packet().unwrap().unwrap(),
        packet(1, 1024, &[30, 40])
    );
    r.verify_remaining().unwrap();
    let summary = r.summary();
    assert!(summary.verified);
    assert_eq!(summary.layout_source, "cookie");
    assert_eq!(summary.edit_count, 7);
}
#[test]
fn every_container_truncation_is_rejected_with_a_file_position() {
    let raw = fixture();
    for end in 0..raw.len() {
        let tmp = Shared::new(&raw[..end]);
        let e = open(&tmp).err().unwrap();
        assert!(e.position.is_some(), "{end}: {e}");
    }
}
#[test]
fn terminal_unknown_data_and_reordered_chunks() {
    let raw = fixture();
    let tmp = Shared::new(&raw);
    let r = open(&tmp).unwrap();
    let data = r.structure.chunks[b"data"];
    let pakt = r.structure.chunks[b"pakt"];
    let mut moved = raw[..(pakt.offset - 12) as usize].to_vec();
    moved.extend(&raw[(data.offset - 12) as usize..]);
    moved.extend(&raw[(pakt.offset - 12) as usize..(data.offset - 12) as usize]);
    let t = Shared::new(&moved);
    open(&t).unwrap().verify_remaining().unwrap();
    tmp.change(data.offset - 8, &(-1i64).to_be_bytes());
    open(&tmp).unwrap().verify_remaining().unwrap();
}
#[test]
fn metadata_audio_boundaries_and_lengths_cannot_change_after_open() {
    for kind in ["data", "kuki", "pakt", "desc", "grow", "truncate"] {
        let raw = fixture();
        let tmp = Shared::new(&raw);
        let mut r = open(&tmp).unwrap();
        match kind {
            "grow" => tmp.change(raw.len() as u64, &[1]),
            "truncate" => tmp.truncate(raw.len() as u64 - 1),
            "data" => tmp.change(r.structure.chunks[b"data"].offset + 5, &[31]),
            "pakt" => tmp.change(r.structure.chunks[b"pakt"].offset + 24, &[2, 1]),
            _ => {
                let tag: [u8; 4] = kind.as_bytes().try_into().unwrap();
                tmp.change(r.structure.chunks[&tag].offset, &[255]);
            }
        }
        assert!(r.verify_remaining().is_err(), "{kind}");
    }
}
#[test]
fn duplicate_and_invalid_structures_never_get_guessed() {
    let raw = fixture();
    let tmp = Shared::new(&raw);
    let r = open(&tmp).unwrap();
    for tag in [*b"desc", *b"kuki", *b"pakt", *b"data"] {
        let c = r.structure.chunks[&tag];
        let mut bad = raw.clone();
        bad.extend(&raw[(c.offset - 12) as usize..c.end() as usize]);
        assert!(open(&Shared::new(&bad)).is_err());
    }
    for (offset, bytes) in [
        (4, vec![0, 2]),
        (6, vec![0, 1]),
        (8, b"free".to_vec()),
        (12, (-2i64).to_be_bytes().to_vec()),
        (
            r.structure.chunks[b"pakt"].offset,
            (-1i64).to_be_bytes().to_vec(),
        ),
        (r.structure.chunks[b"pakt"].offset + 24, vec![0, 3]),
    ] {
        let t = Shared::new(&raw);
        t.change(offset, &bytes);
        assert!(open(&t).is_err());
    }
}
#[test]
fn unknown_chunks_are_skipped_but_their_headers_are_checked() {
    let mut raw = fixture();
    raw.extend(chunk(b"test", &[1, 2, 3, 4, 5]));
    let t = Shared::new(&raw);
    let mut r = open(&t).unwrap();
    assert_eq!(r.summary().skipped_chunks, 1);
    r.verify_remaining().unwrap();
    let mut r = open(&t).unwrap();
    t.change(raw.len() as u64 - 13, &999i64.to_be_bytes());
    assert!(r.verify_remaining().is_err());
}
#[test]
fn stereo_layout_and_cookie_must_agree_with_description() {
    let mut raw = fixture();
    let mut chan = STEREO.to_be_bytes().to_vec();
    chan.extend([0; 8]);
    raw.extend(chunk(b"chan", &chan));
    let t = Shared::new(&raw);
    let r = open(&t).unwrap();
    assert_eq!(r.summary().layout_source, "chan");
    let offset = r.structure.chunks[b"chan"].offset;
    t.change(offset, &((102u32 << 16) | 2).to_be_bytes());
    assert!(open(&t).is_err());
    let t = Shared::new(&fixture());
    t.change(20, &44100f64.to_be_bytes());
    assert!(open(&t).is_err());
}

#[test]
fn explicit_layout_correction_preserves_original_bytes_and_checks_them_again() {
    let requested = ChannelLayout::discrete(2).unwrap();
    let missing = CafReader::new_with_layout(Shared::new(&fixture()), &requested).unwrap();
    assert_eq!(missing.summary().layout_source, "explicit_cookie_layout");
    let audit = missing.summary().layout_override.unwrap();
    assert!(audit.original.is_none());
    assert_eq!(audit.original_matched_cookie, None);

    for tag in [STEREO, (102 << 16) | 2, (147 << 16) | 6] {
        let mut raw = fixture();
        let mut chan = tag.to_be_bytes().to_vec();
        chan.extend([0; 8]);
        raw.extend(chunk(b"chan", &chan));
        let source = Shared::new(&raw);
        assert_eq!(open(&source).is_ok(), tag == STEREO);
        let mut reader = CafReader::new_with_layout(source.clone(), &requested).unwrap();
        assert_eq!(reader.track().cookie, COOKIE);
        assert!(reader.track().layout.equivalent(&requested));
        let audit = reader.summary().layout_override.unwrap();
        assert_eq!(audit.original_matched_cookie, Some(tag == STEREO));
        let original = audit.original.unwrap();
        assert_eq!(original.tag, tag);
        assert_eq!(original.bitmap, 0);
        assert_eq!(original.description_count, 0);
        assert_eq!(original.sha256, format!("{:x}", Sha256::digest(&chan)));
        reader.verify_remaining().unwrap();
        assert!(reader.summary().verified);
        assert_eq!(source.bytes(), raw);
        reader.rewind();
        source.change_quietly(
            reader.summary().chunks[b"chan"].offset,
            &((103u32 << 16) | 2).to_be_bytes(),
        );
        assert!(
            reader.verify_remaining().is_err(),
            "overridden chan still belongs to the integrity digest"
        );
    }
}

#[test]
fn explicit_layout_cannot_change_codec_or_repair_broken_structure() {
    for layout in [
        ChannelLayout::discrete(1).unwrap(),
        ChannelLayout::tagged((102 << 16) | 2, 2, None),
    ] {
        assert!(CafReader::new_with_layout(Shared::new(&fixture()), &layout).is_err());
    }
    let requested = ChannelLayout::discrete(2).unwrap();
    let mut bad = fixture();
    let mut chan = STEREO.to_be_bytes().to_vec();
    chan.extend(0u32.to_be_bytes());
    chan.extend(1u32.to_be_bytes()); // Missing the claimed description.
    bad.extend(chunk(b"chan", &chan));
    assert!(CafReader::new_with_layout(Shared::new(&bad), &requested).is_err());
    for (offset, bytes) in [
        (20, 44100f64.to_be_bytes().to_vec()),
        (44, 6u32.to_be_bytes().to_vec()),
    ] {
        let source = Shared::new(&fixture());
        source.change(offset, &bytes);
        assert!(CafReader::new_with_layout(source, &requested).is_err());
    }
}
#[test]
fn packet_length_varints_are_bounded_and_cover_data_exactly() {
    for sizes in [
        vec![0, 3],
        vec![1, 1],
        vec![2, 2],
        vec![0x81],
        vec![0xff; 10],
        vec![0x80; 11],
        vec![1, 2, 0],
    ] {
        let raw = fixture();
        let t = Shared::new(&raw);
        let r = open(&t).unwrap();
        let p = r.structure.chunks[b"pakt"];
        let d = r.structure.chunks[b"data"];
        let mut bad = raw[..(p.offset - 12) as usize].to_vec();
        let mut payload = raw[p.offset as usize..p.offset as usize + 24].to_vec();
        payload.extend(sizes);
        bad.extend(chunk(b"pakt", &payload));
        bad.extend(&raw[(d.offset - 12) as usize..]);
        let tmp = Shared::new(&bad);
        assert!(open(&tmp).is_err());
    }
}

#[test]
fn a_rewound_pass_repeats_the_packets_and_is_verified_again() {
    let tmp = Shared::new(&fixture());
    let mut r = open(&tmp).unwrap();
    let mut first = vec![];
    while let Some(packet) = r.next_packet().unwrap() {
        first.push(packet);
    }
    let summary = r.summary();
    assert!(summary.verified);
    r.rewind();
    assert!(!r.summary().verified);
    let mut second = vec![];
    while let Some(packet) = r.next_packet().unwrap() {
        second.push(packet);
    }
    assert_eq!(first, second);
    let again = r.summary();
    assert!(again.verified);
    assert_eq!(
        (again.audio_sha256, again.packets_sha256),
        (summary.audio_sha256, summary.packets_sha256)
    );
    // A change between passes is caught when the next pass ends.
    r.rewind();
    tmp.change(r.structure.chunks[b"data"].offset + 5, &[31]);
    assert!(r.verify_remaining().is_err());
}

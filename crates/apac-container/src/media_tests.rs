//! Media opens without reading audio and reads the same packets as the
//! verified readers, from any saved cursor, with the same rejections.
use super::*;
use crate::{
    CafReader, Mp4Reader, Packet, PacketSource,
    test_source::Shared,
    test_streams::{Fenced, LAYOUTS, Stream, hex, mp4, streams},
};

/// Every packet from `cursor` to the end, each with the cursor before it.
fn read_from<R: Source>(
    media: &mut Media<R>,
    mut cursor: PacketCursor,
) -> Vec<(u64, Vec<u8>, PacketCursor)> {
    let mut out = vec![];
    let mut bytes = vec![];
    loop {
        let before = cursor;
        let Some(index) = media.read_packet(&mut cursor, &mut bytes).unwrap() else {
            assert_eq!(cursor.packet(), before.packet());
            return out;
        };
        assert_eq!((before.packet(), cursor.packet()), (index, index + 1));
        out.push((index, bytes.clone(), before));
    }
}
fn verified<S: PacketSource<Error = Error>>(mut source: S) -> Vec<(u64, Vec<u8>)> {
    let mut out = vec![];
    while let Some(Packet { index, bytes, .. }) = source.next_packet().unwrap() {
        out.push((index, bytes));
    }
    out
}
/// The stream as CAF and in every MP4 layout.
fn files(stream: &Stream) -> Vec<(String, Vec<u8>)> {
    let mut out = vec![(format!("{} caf", stream.name), stream.file.clone())];
    for layout in LAYOUTS {
        let file = mp4(&stream.cookie, &stream.packets, layout).unwrap();
        out.push((format!("{} mp4 {layout:?}", stream.name), file));
    }
    out
}
fn track_fields(track: &Track) -> impl PartialEq + std::fmt::Debug {
    (
        track.sample_rate,
        track.channels,
        track.layout.tag,
        track.packet_count,
        track.table,
        track.file_bytes,
        track.cookie.clone(),
    )
}

#[test]
fn packets_equal_the_verified_readers_from_every_saved_cursor() {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../../data/mp4-reader-fixture.json")).unwrap();
    let mut inputs = vec![("mp4 reader fixture".to_string(), hex(&fixture["hex"]))];
    for stream in streams() {
        inputs.extend(files(&stream));
    }
    for (name, file) in inputs {
        let mut media = Media::open(Shared::new(&file)).unwrap();
        let expected = if media.container() == "caf" {
            let reader = CafReader::new(Shared::new(&file)).unwrap();
            assert_eq!(track_fields(media.track()), track_fields(reader.track()));
            verified(reader)
        } else {
            let reader = Mp4Reader::new(Shared::new(&file)).unwrap();
            assert_eq!(track_fields(media.track()), track_fields(reader.track()));
            verified(reader)
        };
        let start = media.start();
        let all = read_from(&mut media, start);
        let packets: Vec<_> = all.iter().map(|(i, b, _)| (*i, b.clone())).collect();
        assert_eq!(packets, expected, "{name}");
        assert_eq!(
            media.track().max_packet_bytes as usize,
            packets.iter().map(|(_, b)| b.len()).max().unwrap(),
            "{name}"
        );
        for (k, (_, _, cursor)) in all.iter().enumerate() {
            let rest: Vec<_> = read_from(&mut media, *cursor)
                .into_iter()
                .map(|(i, b, _)| (i, b))
                .collect();
            assert_eq!(rest, packets[k..], "{name} from {k}");
        }
    }
}

#[test]
fn opening_reads_no_audio() {
    for stream in streams().iter().take(3) {
        let reader = CafReader::new(Shared::new(&stream.file)).unwrap();
        let data = reader.summary().chunks[b"data"];
        let mp4 = mp4(&stream.cookie, &stream.packets, LAYOUTS[1]).unwrap();
        let mdat = mp4.windows(4).position(|w| w == b"mdat").unwrap() as u64 + 4;
        let audio = stream.packets.concat().len() as u64;
        for (file, fence, operation) in [
            (
                &stream.file,
                data.offset + 4..data.offset + data.bytes,
                "CAF input",
            ),
            (&mp4, mdat..mdat + audio, "MP4 input"),
        ] {
            let fenced = || Fenced::new(Shared::new(file), fence.clone());
            assert!(CafReader::new(fenced()).is_err() && Mp4Reader::new(fenced()).is_err());
            let mut media = Media::open(fenced()).unwrap();
            assert_eq!(media.track().packet_count, stream.packets.len() as u64);
            let mut cursor = media.start();
            let error = media.read_packet(&mut cursor, &mut vec![]).unwrap_err();
            assert_eq!(
                (error.operation, error.message.as_str(), error.packet_index),
                (operation, "unreadable region", Some(0))
            );
            assert_eq!(cursor.packet(), 0);
        }
    }
}

/// Rebuild a CAF with `extra` appended to the payload of chunk `tag`.
fn grow_chunk(file: &[u8], tag: &[u8; 4], extra: &[u8]) -> Vec<u8> {
    let mut out = file[..8].to_vec();
    let mut at = 8;
    while at < file.len() {
        let size = u64::from_be_bytes(file[at + 4..at + 12].try_into().unwrap()) as usize;
        let mut payload = file[at + 12..at + 12 + size].to_vec();
        if &file[at..at + 4] == tag {
            payload.extend(extra);
        }
        out.extend(&file[at..at + 4]);
        out.extend((payload.len() as u64).to_be_bytes());
        out.extend(payload);
        at += 12 + size;
    }
    out
}

#[test]
fn table_end_and_header_rejections_match_the_verified_readers() {
    let stream = &streams()[0];
    // A packet table entry past the last packet, and audio past the last
    // packet: both are found when a cursor reaches the end of the table.
    for (tag, extra) in [(b"pakt", [1]), (b"data", [0])] {
        let file = grow_chunk(&stream.file, tag, &extra);
        let expected = CafReader::new(Shared::new(&file)).err().unwrap();
        let mut media = Media::open(Shared::new(&file)).unwrap();
        let mut cursor = media.start();
        let mut bytes = vec![];
        let error = loop {
            match media.read_packet(&mut cursor, &mut bytes) {
                Ok(Some(_)) => {}
                Ok(None) => panic!("table end accepted"),
                Err(error) => break error,
            }
        };
        assert_eq!(error, expected);
        assert_eq!(cursor.packet(), stream.packets.len() as u64);
    }
    // A header rejection: the description's format ID.
    let mut file = stream.file.clone();
    file[8 + 12 + 8] ^= 1;
    let expected = CafReader::new(Shared::new(&file)).err().unwrap();
    assert_eq!(Media::open(Shared::new(&file)).err().unwrap(), expected);
    let mp4 = mp4(&stream.cookie, &stream.packets, LAYOUTS[0]).unwrap();
    let mut file = mp4.clone();
    let soun = file.windows(4).position(|w| w == b"soun").unwrap();
    file[soun] = b'v';
    let expected = Mp4Reader::new(Shared::new(&file)).err().unwrap();
    assert_eq!(Media::open(Shared::new(&file)).err().unwrap(), expected);
    // A cursor of the other container is refused.
    let mut caf = Media::open(Shared::new(&stream.file)).unwrap();
    let mp4 = Media::open(Shared::new(&mp4)).unwrap();
    assert_eq!((caf.container(), mp4.container()), ("caf", "mp4"));
    let mut cursor = mp4.start();
    let error = caf.read_packet(&mut cursor, &mut vec![]).unwrap_err();
    assert_eq!(
        (error.operation, error.message.as_str()),
        ("packet input", "packet cursor belongs to another container")
    );
}

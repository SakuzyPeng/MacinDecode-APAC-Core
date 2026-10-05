"""Approved, frozen batch outputs for production source migration only.

Discovery never imports this registry. These pins were recorded after candidate
freezing, held-out validation and the final independent reference comparison.
"""

BATCH_SOURCE = dict(
    experiment='hoa-blackbox-batch-v1',
    code_commit='452661d7d7648227e444638e13bdedee3511fd4e',
    tool_revision='uncommitted source snapshot identified by tool_fingerprint',
    tool_fingerprint='5ab7c7613fc58a0b7ebe12b60161bd4ff14b751a536962871e3330c9dfbdb2bf',
    analysis_policy='hoa-blackbox-order3-q6-weighted-v1',
    policy_sha256='e8b99180672e7714d331d5b76b0a9dfcf86724681a637aa19c41e347b6e86110',
    native_identity_sha256='f73b54d6e731c11be5987a0e659c492f9f8edc473180305dbd41f57edcb8f7f7',
    binary_sha256='8878d0c03c0889a70c7d352ce4a084e0f495478239f6c40c58cb3663ee302500',
    component='AudioCodecs 7.0',
    component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',
    system_version='macOS 27.0 / 26A428',
    architecture='arm64',
    candidate_frozen_before_comparison=True,
)

OUTPUTS = {
    1: dict(
        codebook_candidate='795efd7ae66b30a1ac7bd42c15c26020ce97546de52218614a2e62d266cf3767',
        matrix_candidate='cddc91c8a3bd534e26d0d03434674c2fa3c729fd83e44cfeb3a370b861924967',
        codebook_values='6e04f7a58d699d3e08666b078afd3e77b8e181ce4a1fd30e01effa5732e58cea',
        matrix_values='a3cfa1e9321c82986dbf97ed2ba8f2c9b86be9095962f84b3e5d33f36233ab2b',
        validation='055574a42b1a96ba6e554a01b2ca29978948084a35922cb666227815b78052ab',
        comparison='01104aae3e0788266685da46bbf3c99ee92045e0988be30e075786f8e23c2383',
    ),
    2: dict(
        codebook_candidate='83a280b5264d9c92613be8302b68e24bb8a6c849b47cb1fb342a674058f0fade',
        matrix_candidate='95121ecb869fc8ad2a528a74e2005d4214007dbabaab91d4b94c42bbfa78fac2',
        codebook_values='07959b3786362b47eb1380e587c7ab3fba4e0c094b0221652ac30ee798be9d32',
        matrix_values='e81d7a49eb0e162933d90c2378ca745012be336b5eb6485af79dca4d512c75d4',
        validation='61121a3061f561b45c31629345adf264d976cc28e6b1f07294717ce996c75a3d',
        comparison='28c6ce3fd2a6ac7bb10e1692450db6636817ea8a8e3e7e3285ad48af3535307d',
    ),
    3: dict(
        codebook_candidate='08c76d4ce954d650986c2a30f4120a8d631f62b7bce0ac74fb5bb87b15f94086',
        matrix_candidate='b405fbaebecb635b83943187021d571fb513d72233ff1826664322cc34031dfa',
        codebook_values='6ed393ceffa5d005b34939fe9c0673197c39bcd09ac0619b011ad9d1df8c0327',
        matrix_values='ff82131f4cdc56f49559c5bdeb7dbf67dd9c5ffcd09d3f8b16bd0a2899ba95b3',
        validation='05f2c42663b8f7925f795d01be5c323a3f865a0c751b3ea25b5f31ea0cf41962',
        comparison='e786bf48b814e5ec4a44b8698c8942f88b6151bafc3ecb22dcf8fff815966e96',
    ),
}


def source(cluster, kind):
    if kind not in ('codebook', 'matrix'):
        raise ValueError('unknown batch source kind')
    record = OUTPUTS[cluster]
    result = dict(BATCH_SOURCE,
        method='public AudioConverter PCM black-box reconstruction',
        candidate_sha256=record[kind + '_candidate'],
        validation_sha256=record['validation'], comparison_sha256=record['comparison'])
    if kind == 'matrix':
        result.update(
            method='public AudioConverter PCM reconstruction with frozen calibration weights',
            measurement_quantization_bits=6,
            max_empirical_half_width=2.3251493876046068e-7,
            empirical_half_width_limit=2.5e-7, held_out_checks=254,
        )
    else:
        result['validation_scope'] = 'Huffman codewords and lengths'
    return result

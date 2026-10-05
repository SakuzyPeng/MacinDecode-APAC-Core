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


MODE23_OUTPUTS = {
    (2, 0): dict(
        candidate='c19bf56a443252874eb675010eff0beee1f27e2417dc5a0d6a4842fbdca2a6b7',
        book_values='b6454d2e72fa0d4379ed35a0654117a6d0d642e7120aa361e743215a668e8e51',
        group_values='fa1a8d0500bded3fe41cff7249eb8a9ac45940f271b94677f7f2989fc322aad6',
        layout='9f2424b31abf465f418f5e4f1438acefe19a1b48483778cfebd8b0dabe3cd229',
        validation='d6fcde7a216c0408d095088a79f21834a844dce9664b29e15835d6aeb9af9a57',
        comparison='e34a177871f325fadb94bfa17b8931db55573b75f9f3167609466cee8e8cb16e',
    ),
    (2, 1): dict(
        candidate='1e3807e1dabb7f1f47c21548d2e079c4675b73358680c5039c34691b21c8ed1e',
        book_values='65c66abc4fee011b32b4bc05d03c7fb94de4cf3ece3de4308e82a0cac8be86af',
        group_values='302afc300ad5263c92900f3042be1833fa550b67b29d135125db0012227327a2',
        layout='9f2424b31abf465f418f5e4f1438acefe19a1b48483778cfebd8b0dabe3cd229',
        validation='248e71d42e16b0ed04a9b4a8f291b26bf2b42b730e9df70739b71b9e549fd7ce',
        comparison='7da9679a268e69c61f52b700aa2eddf621f7c46e50cd9a0b99c0e599f2774ed7',
    ),
    (3, 0): dict(
        candidate='1240d891d4cf056cffa9c84d59ae9a1c25b8147adcc4bd2cbf364880d40838ff',
        book_values='a987e10a45914106ab796d31fc7587a3d997310d09aab04a02f66a7c27c899d1',
        group_values='4939a292c2f5164ddcf27a07ddc7ef96928baa3e8967453a7294a9ecebf0a5c3',
        layout='f02a0309f1939fc5c04a8df91897bfd4ec05946db3a503d34008ecb8e3455870',
        validation='b867f13632e74a0feaed8f6bc1150b952c80ab3728dfa283ca5bfbbc39e1bcdb',
        comparison='2b22858114f08d0e75a9680a76147f16ad3e679b9f1d0340f767c2b99684de94',
    ),
}


def mode23_source(key):
    record = MODE23_OUTPUTS[key]
    return dict(BATCH_SOURCE,
        method='public AudioConverter PCM black-box reconstruction',
        experiment='hoa-blackbox-order3-q6-mode23-v1',
        tool_code_commit='8b1b3c631fa5e0e27fa349d9729be724c50402e2',
        tool_fingerprint='39454e05844f61d1d22a93c149f79a056b26a2341582adc2683b72a40c4b02dd',
        analysis_policy='hoa-blackbox-order3-q6-weighted-mode23-v2',
        policy_sha256='7c66c4680d5198618e857f7d22a29ec9094367ce8f95afbd9a4e0b95bf696c9e',
        candidate_sha256=record['candidate'], layout_sha256=record['layout'],
        validation_sha256=record['validation'], comparison_sha256=record['comparison'],
        validation_scope='Huffman codewords, lengths and coefficient order')


Q7_OUTPUTS = {
    (1, 0): dict(
        candidate='34726d9db8b252979fb0011a999d647b7e9c92ae5b9981fbe7a625071b995521',
        book_values='a71142dc81e6729626b0f02747593a555139dc6f91d1636cb0abc4de3c4c20b0',
        validation='3852d13fbd8914f51342b94e5f3f6f66b1ce59ad758994e6feffc66bfff6393a',
        comparison='401279e55628b20b8878d3727ffff87fc74815c583b50827314ce9a2ec2effec',
    ),
    (2, 0): dict(
        candidate='2d5912fbfc151a50090999b2864d546446d360e85fd25becc5b0984eb70db868',
        book_values='38e93984c09b6b68b27ffb8110efcc57ffb881c2af347aaaae99c99a22693c8d',
        validation='9eab4f747a240037b1c982e7655b70c5cb91842e2dd510149c94c60178d3e336',
        comparison='0828b702540b41d6da4042335e87b54af3b1215bd15c6e872c88cf0989e05266',
    ),
    (2, 1): dict(
        candidate='c80d6c18a3a6359f2dc10aa61cf9bf1cf9fd54fcb3d9f76d36e165248ff72964',
        book_values='c76ea7f0a66503828ce9cb6b0d7bebfeefcf4941d1e66a9a4e965c238052c664',
        validation='98e56475d9d35001f78b32e364de6115f7b0d2068e88f002fbaeae502793aa44',
        comparison='3d8da38d75426ab158bd930f52cc030d69d74242997c77f588417d18f1a14621',
    ),
    (3, 0): dict(
        candidate='78cf01cc9783cc463c9f9f687ce72b84c29453678e2502d8781ceab25858beb7',
        book_values='7895bb8683079eb2d0c91e2c3494a0b4e04ebfd96e9891c5935a8d83c4bb8a19',
        validation='9029d69a15f7da4206b047449ee31fc132ec72b8799321ba394c00d0a8de29e3',
        comparison='2d44bd321487b5a9e3c394eb60506dd9a65984085dfc7061ae5edc08ebd9bb7d',
    ),
    (4, 0): dict(
        candidate='8b223e19e9f2d7bd74518cc17cc9a564d431298e83b33215514ca50ef80141f2',
        book_values='994c707b790a40eb9332fb997b08fe5ae2745b8a386093dae622f1cda14c306a',
        validation='be13b71bd8cf4a412fb2af9eee3ed01498d980d58b14e88a1b941c959928c6ed',
        comparison='fa68277f638a710c2636a41b07add6e33376236df4c44741cb83459cc1c5769b',
    ),
    (4, 1): dict(
        candidate='87d02c7f44d95256b9667657fe1e98b6fa1090d40e91d8ee5fd22b298d18ee03',
        book_values='59d76118b47881eeb2cda1f0d13296ebd6bfe26b81b9b001973f936417050ab9',
        validation='8e0ebc0c1876edff99f70e69f0ea830545c7b35a7e5c538c08f68c49887038a9',
        comparison='e1d8bffc1415120e85120b0038ffc6ce6418667d0610e0460c2f39b2e2748097',
    ),
    (4, 2): dict(
        candidate='ab5498d88c5aee68ca8cf0f665d1b50c30c38b7bae87bec0f15a84727a969e09',
        book_values='c38a07cc2a7d097c8d3f694bce8e659360f8abf63a28b4eb8d3d46a4b0e02988',
        validation='dfb24374288adcee0ca68d5ca3af66a868cd91f6c221eb5a732897790c55ed04',
        comparison='685fb95226d5b9212f451a12346b1ce4ba834e45d577226c41cc358147479343',
    ),
    (4, 3): dict(
        candidate='fc3c5ed217c871f10d9d28f24f07ef86de10e39ee52380e49f250020707e1ea5',
        book_values='410e970c4326fe62f1ad7aaf2eafd1a34d865b65e89d52e60835f605642c549d',
        validation='29a052e887822ef215b5bdfa1fac9b64e3dd983fcbfc6c8a176ecd7c18c461ad',
        comparison='ed4e693a8f722459c954fd95c7d8b63511afdb22e16a7b5c08a520842ff19188',
    ),
}


def q7_source(key):
    record = Q7_OUTPUTS[key]
    source = dict(BATCH_SOURCE,
        method='public AudioConverter PCM black-box reconstruction',
        experiment='hoa-blackbox-order3-q7-v1',
        code_commit='2fdc922f35c79caf977b4df1002883aa59322f05',
        tool_fingerprint='d4912dfc8a5a097937455ad8b3e0cb54b8d6bac60348877aa615a218be06cc1f',
        analysis_policy='hoa-blackbox-order3-q6-q7-qualified-priors-v4',
        policy_sha256='c02cf23bae8da441b03ce28e8d7fff2693c34f1f2059dbd512930ae8e9498a77',
        native_identity_sha256='6bde6f1da71efc79e86ace4ce408a557cbf4968a7a9d37bb6d5c8b2c6aef6369',
        binary_sha256='493fd0a9768a88edfb3b4971b96c36914ed32e71f51fcddd5371837660604856',
        candidate_sha256=record['candidate'], validation_sha256=record['validation'],
        comparison_sha256=record['comparison'], validation_scope='Huffman codewords and lengths',
        matrix_reused=key[0] == 4, group_reused=key[0] in (2, 3))
    if key[0] != 1:
        source.update(prior_quantization_bits=6,
                      prior_sha256='206e529b31d5410ecec3fef16c49f83ff29d31ade233a2520aa5ba9195c16e69')
    return source


# Eight- and nine-bit pins from frozen, validated and compared candidates.
Q89_OUTPUTS = {
    (8, 1, 0): dict(
        candidate='fc7532d4492ee3e4195c2ef775dfbd38107cd53746e34e36175857ed0160d5f0',
        book_values='8bd37ce28df300eaa7f0169a5ca36dfc45aab90c668dcfa1bc6f55c2e4e14b6a',
        validation='47841bdf63f4e79f601e0986d4604b281ccd02b9de10a302d0196554a30b2332',
        comparison='626444a2e3477d1ad611d451adfe356cc34627a3f2475638e72c840b6a11fee0',
    ),
    (8, 2, 0): dict(
        candidate='26158578693ec15ac08fa9dbdeab1e7865111cf9942abc10d214c0addd547539',
        book_values='0d0c684387bc0af50a548e9f014e56e605d22369e8ce5fcd314a6663675de041',
        validation='71039aecae97258c5109055a67bbde3e8cc40a363ee482c42c1933c428a3cf27',
        comparison='d8a7ab615c6544b6f00619afcb5af5e1540ddb2dc638867db989dc507f7c2440',
    ),
    (8, 2, 1): dict(
        candidate='a91e32b3278d0e59e6205bf12b81321b3748d7679edfa63c48f595f887eedbe3',
        book_values='c19f493f6231aefa6b4584f4e730e2b75f0fedeeadb8bb609646b740c12c785c',
        validation='67b6ef1fe14c8a86d07e4d9f29c8e9528ec591ea84c04d2ad11b8899a085349e',
        comparison='7bde694a4dbe98b99019fef6e5770fe864453478d3e9fd2dd9437a4775966796',
    ),
    (8, 3, 0): dict(
        candidate='fd8c0a31aa5e3f8ac7e223b333fa8c02260c35746075eacf91cfb901d83c1769',
        book_values='55ade17b5d15c017a39a01267b08d985bb144e163dcef4014b50914d5a37e6d2',
        validation='3c5f8bfed9c5e49799c58e063c821dd8eed91cd174136cf207d9d07fe906545b',
        comparison='0a1998e3a2178522c2966acdefc424a839c35ffb4329c9ee146343d6ebc8a0bb',
    ),
    (8, 4, 0): dict(
        candidate='f27ed53295beade915a29cbca869c179f85ac53dc47438cab456bf16aa356dec',
        book_values='4d088ad904bce6e0651b30bd04fd13832216076eb5faeb40b776e81fc728d574',
        validation='50b8f87a927ae1a32c3eefdcd18f66074d23a544b7da04efe8ae8855d818282c',
        comparison='a71600232d221d3fc5fd97ecf4ee71677314478460013c2286fdfb39e8a8c560',
    ),
    (8, 4, 1): dict(
        candidate='451cca75d18add8f075f6bfa3fd94245baaaec106ab3fc50848158621526321e',
        book_values='950bb4f93c516bdf0334d3a0e0995195e783e1a906bd2e9e701dd60933c63bcf',
        validation='e86095ed5ab30efb1833e999688f8c216f9b44829e94fe69c63c12c0a2d90ccc',
        comparison='140999c1b95a5d81a390acc976f39a1a2efeed247d2e05b83bda8b3fa4b4264c',
    ),
    (8, 4, 2): dict(
        candidate='0e4014a4ca206c30d6e800c16a2bd4e20a6cca3a4c045cd93bc52e8a496830a9',
        book_values='108333b9f72498b0e5f4f0db2f9db6de5800cc5b7f7f32250c99a00a90033b79',
        validation='93f1129beb50c7d1c6ffb8c00d50f01d6edeb4074b4bb6adfb0b79fe536ccd76',
        comparison='5f98ece742e7a03e39ba37bd79ef901d5dc3d2999001c85cd1e9340f87c7b1af',
    ),
    (8, 4, 3): dict(
        candidate='a17aac35dbd943cf221b2d25901ea6cb5b854d10629073ea2f4b10c4d45d28b1',
        book_values='c80c2b6e059c40caf9893e7036284e92440dd358b4a2f60fc9413a98def9e29b',
        validation='5725f2b2cd502ae5e78105588b8dc791c67c085e34b90576f7c600af5d10a098',
        comparison='7885462ddea59714dc610c7c5abbc90249b66b770f6a574bd71fcf79770a505c',
    ),
    (9, 1, 0): dict(
        candidate='59578ae643c559519bc107a33d605340695039cb274ab3e6ef21d6850f4349b9',
        book_values='7b8944ad075dc444c6fe38b392472c1cd261f253d7195f013b14419a3fd56081',
        validation='30db578aad8213136e5aee6fd909ed410c88cd1d8d602da23e5ebd189f3fd5c0',
        comparison='0d28627b1202a585c6164c79e8c6eb8fabb04ed139a0df032806aee2e78f9dc5',
    ),
    (9, 2, 0): dict(
        candidate='ccec51d2d65e100403e4f29c18e1156839e20b1231040eb88543f85fe340a5b3',
        book_values='1c00698469f08e3695a83f462ac790fb8701d618a8a3c7f53ab2a5616916be69',
        validation='23b1b7f3e12692f94d2658c00eb3e6420e7a8d1767baa22106c3896c42ae677e',
        comparison='fb18aaed3fabc74245b10bceaad16b4fd0b03ff86a0855469eb73a88bd4ceb31',
    ),
    (9, 2, 1): dict(
        candidate='3ecd449031a5d4c2f1447c04bc675b66e6c6e8290b8f2e5decad5ba97c789507',
        book_values='281093078f9bbf9b8993d4123dcbd68a97897f42e33d28601253917ec6d15f8d',
        validation='f5a565a05c9faccb5fbacf933938c07532175a15284d8beb1c823ec8b3a22e46',
        comparison='20b2599ab59ccddd550e9eeede90deb3c5c9957bfd3c073f2755f4d692dfbd89',
    ),
    (9, 3, 0): dict(
        candidate='15af8c0c1eba1169f829b2bb8c635147d2b6fa14e45c16297393a2f7764e4ab3',
        book_values='8ce05c719252922d6099be573bad6f3e59d976ac1142a45d5a7bf3754e8b0a43',
        validation='9dc275fe7c21a85e52437060cdc415dee7ddcc5560525acb33d56a5cdc2f428c',
        comparison='f34e40b345aaf5812d3817efc8e95b6d08d1515c2e53655d2e50a8da3f3d03ea',
    ),
    (9, 4, 0): dict(
        candidate='b8714640c877d952dc8904e4b580210644a155a942a458d8e3c18e654b542a7a',
        book_values='3eda12c8bd891aa6b3f15ce780bf486ceec977949d5e5a8a79a4e88c76c832ea',
        validation='f2613134ca4007ed630dfaf002443be1bb1c00d7f22084404867a92af4509e2e',
        comparison='d14171edb90439794f5f3f6a4dab162bba728367fa62ed713784ad1af60f45fd',
    ),
    (9, 4, 1): dict(
        candidate='f4722f070d7d2567c4f227efdd780aa8925e7c12de81134eb59762a3fed13d95',
        book_values='2c64768c1ea958f6f6f5fec9aab9e9fac877c9f1cd1dc31194b990cdc606402a',
        validation='89a13b514ff3c9307f18baa92cc9dee7988de27b67d45f256b62379037491d6e',
        comparison='195dd75117789aaf6861fa3b88ccf44c71addb2729d3cdecd62bdc3aed9d7f8f',
    ),
    (9, 4, 2): dict(
        candidate='5c0028f1078c933aa86cf6f4622407cc60a4a2caa1477082684920b9e7e4bde3',
        book_values='c44b293e084da28c1236532fa07b5f39bf8d9f3dd6878fef382f1f619af2c085',
        validation='def99237634b52438105b959b4a9ec40700f64cbf89654fbb8d64e3257406f41',
        comparison='5e74e76c8a52b4505ad8bc25b9560e64d5e142085113c3bc3d24e046c6c508e6',
    ),
    (9, 4, 3): dict(
        candidate='5d24a882c5502f3f490f34b77a36c3a51f78d6f1c61f06a1883dd65c3a313411',
        book_values='100c17110ad55b062eae941500670410c8da1111e3012c84f1f7cd6c61bd9b2c',
        validation='5be472a996ebfbde0506c85ccae6c47148aa25c6a7b73681559fb2f84c3058e5',
        comparison='2f2bfbb853b7481e6c798f6f975af56f04aa6dae6ee90d56c68fe025c0c173a8',
    ),
}

Q89_SOURCES = {
    8: dict(BATCH_SOURCE,
        experiment='hoa-blackbox-order3-q8-v1',
        code_commit='584050484b755d46bc4e426f1a0f91e0da890b07',
        tool_fingerprint='4402cc843dff5b9baf8895c510b29d55c8da1669d7f5875372a181b2842abf52',
        analysis_policy='hoa-blackbox-order3-q6-q9-qualified-priors-v5',
        policy_sha256='3cbb6cb854fc47aba198ee97792b97bb08cac2568b4005e6b1c498af06a893a2',
        method='public AudioConverter PCM black-box reconstruction',
        validation_scope='Huffman codewords and lengths',
    ),
    9: dict(BATCH_SOURCE,
        experiment='hoa-blackbox-order3-q9-v1',
        code_commit='584050484b755d46bc4e426f1a0f91e0da890b07',
        tool_fingerprint='4402cc843dff5b9baf8895c510b29d55c8da1669d7f5875372a181b2842abf52',
        analysis_policy='hoa-blackbox-order3-q6-q9-qualified-priors-v5',
        policy_sha256='fc11d16086bc265e36ec93799fd8afb0069e82abeecbe09f70cbc4a8222b5731',
        method='public AudioConverter PCM black-box reconstruction',
        validation_scope='Huffman codewords and lengths',
    ),
}


def q89_source(key):
    record = Q89_OUTPUTS[key]
    source = dict(Q89_SOURCES[key[0]],
        candidate_sha256=record['candidate'], validation_sha256=record['validation'],
        comparison_sha256=record['comparison'],
        matrix_reused=key[1] == 4, group_reused=key[1] in (2, 3))
    if key[1] != 1:
        source.update(prior_quantization_bits=6,
                      prior_sha256='206e529b31d5410ecec3fef16c49f83ff29d31ade233a2520aa5ba9195c16e69')
    return source


# Populated only from fully qualified, frozen order-1/2 experiment outputs.
LOWER_OUTPUTS = {
    (1, 6, 1, 0): dict(
        candidate='eae98d4ef457e569b0115ff82e0b095c00a04d08a2cad8285ee8528a8b18a5a2',
        book_values='0ac031a3830de81423492cec006a2b0efaac7c7dccad6071361f3881cf99e734',
        validation='a4f3574f7b314e44acb81878483c7403e287fd8904c77580b47639df0e8d1987',
        comparison='649b9e5503adcadc82f0598c787bb084892df4383df04dd1dce7e046bf02afcb',
        layout=None,
    ),
    (1, 6, 2, 0): dict(
        candidate='a774e0d01dc6267739b81d07cdb25f6b55c9207da3936d1b89758207fe8f88a0',
        book_values='1b66164a2d661fd9855480ce26dab690b6123a48f0cb64833c27314d7a811c6d',
        validation='94e7587444b10d27379c8d88f09f6f337766c5bea8dd46b389f0d533b481856c',
        comparison='199d46ac65e3c6fa02084dfb1b70b657fd69e974c6ed893ea85ebb571e106dac',
        layout='4fabebcb5e0807d2411125c79f952691eaa42ff12f183e72245ccfd59e518d21',
        group_values='038966de9f6b9a901b20b4c6ca8b2a46009feebe031babc842d43690c0bc222b',
    ),
    (1, 6, 2, 1): dict(
        candidate='4004e41bb78d38bc091dd93fdd420bae152e3dbb5d35efec3749e5cbb4812bf4',
        book_values='e015a2b9dd9dee6974aeba48b76560c560953e2d5b37cd806605029f0f8a535d',
        validation='5df58c40b3ffc7f1b5d4370210d12ff20a40bc9e812d83100178216b46f9dc65',
        comparison='2adc2daec24907b68df245ddd4b212aaef2cde50c9389e564273c71c70c34244',
        layout='4fabebcb5e0807d2411125c79f952691eaa42ff12f183e72245ccfd59e518d21',
        group_values='17f66f2f133c5b3a473a32750ffcef944a00b0581ef1750d412c6f0e26a852ee',
    ),
    (1, 6, 3, 0): dict(
        candidate='dbea72988528818d20592fe0e291924b8eb2034010447d113d1c00a09d7ea129',
        book_values='1bcb349f595f8de7f146c60f0b2885125e3c06b1c37ec19efd65a7809ee242e9',
        validation='a290255419e0927584fb233db85224b40305ebc777551232d7778fd229e5adfc',
        comparison='55e92771279c4854ec57fd02b8da57f7adb81f42bd3a98a72b7aecce500de80c',
        layout='764aa59e33bd862a206ccec1af1616cb64c24ce864a3f6125b56b033480ca685',
        group_values='f2f991d74dfbb2edf7f4475dcb7d82989de00a71e44bb11d4a6fbb49442577b6',
    ),
    (1, 6, 4, 0): dict(
        candidate='53694f231d7bdb77b26f509116f89adf6bc41b44760018c3275800deddbc68b3',
        book_values='b4f354a98d2cda979c5bbd565d224f92500d827389dea742deba9d5ce0ecfcf2',
        validation='76c467bcb2224d2619684fe33ae87efe55dc87c167a65f7d4ccecd24bbf78e03',
        comparison='5b2460b7ab665b815a7e107a5497ee25a0908e8aa40905a9c205373957601145',
        layout=None,
    ),
    (1, 6, 4, 1): dict(
        candidate='7f9ee36d40d4e5ad38226c00270e9d01b09cf16a5b3089d27f15b977d845b3d5',
        book_values='96bcfd440974a2e1577fe04ad58c6be62fad8d5e9ccc9f16f470eeb5318575b5',
        validation='0f146dcfe3fc801148ce67eb84564d5674631f1db02f55d99b8b64fa9f493d1f',
        comparison='074b7b33280a7865056107af9a4430df17d3bfb649bdb046afe5f7d4135ed638',
        layout=None,
    ),
    (1, 6, 4, 2): dict(
        candidate='1e01dc860545745d087664910218aed97431fc67cdac7abca7db204b4f26884c',
        book_values='a42ee77a3c75bf6d21998d0f1085ed7c26c010023301e270fe04a7f88517cd1f',
        validation='8e4fb2d73e4944f83e86d8511a664ca55c9262cf2ee7bbae592abf4d07323a18',
        comparison='7a899cb9a6484ebdf20144ec5ddd9717a94ac78129a205dff52f8325f73474eb',
        layout=None,
    ),
    (1, 6, 4, 3): dict(
        candidate='164ef917a799f43dcc57a0e21dc1b506d53b7926fb646a619facdaeceb2f8785',
        book_values='73a5375f0ffc1920e5c961e2d707b0f8fcef9eb8292bd4a8bc3c576a2f9bde1b',
        validation='b462f21ff9f0f51fa44e0d6fb6672a10ff0f658f86ebc12e90354b7cd6da2823',
        comparison='28ca1caa8eb84af0cf2ecd7b2cbb1fd93e1f6501e5f761ce86a5d005f7f162e5',
        layout=None,
    ),
    (1, 7, 1, 0): dict(
        candidate='20b9da7067fac1df036196884641f290f799363428b0e406d6b2af285db6d971',
        book_values='c6dad7d1aef2cb75650db6253ee88c984bdb6dec250a3d9b853df52e10f662b7',
        validation='29f235641adcd367ad271c10a979597e50f1a0b49d26060fc8c8fcd118bebfec',
        comparison='5141cc94987b43ffb49f504a2189cee4b3391f1af0ae86cb085d0f0e8d2254f4',
        layout=None,
    ),
    (1, 7, 2, 0): dict(
        candidate='94687a24307cc260353167f26885094f9b87b1af527a715508ae5e15559a938b',
        book_values='8aa5381daf6f6921833792d6fc43811e4a2c77618cf73f69726ef553b6cece17',
        validation='5cdb944c927c0472cefc9d66276cdd178a134b4c161f16f1bcc9420cbdc62fa9',
        comparison='f3ee3934415320eb23ba6b1a5df68559d3ec7bb9268bb7e742fab22696e841dd',
        layout='333f2348b2240617945da0e82e230b0772c19ef8d6c32e4d7234b3044df6c680',
    ),
    (1, 7, 2, 1): dict(
        candidate='7f867bbce0e5a90679651945b6d653258214b4b3b8112ae180faa37fbf2495ed',
        book_values='67bd1e6e8427b59613d693168253490fefa3ae882cbf3c83c81f4a71599a3843',
        validation='45192e8adc1f997c4ec0b120b450fde740e7d97e170ec02fb049bfa74acfe76f',
        comparison='6cdc24fc52d3a1f2dccc99e1beb0fc3b62f8aff219d1bc579b7d2f1de873aecd',
        layout='333f2348b2240617945da0e82e230b0772c19ef8d6c32e4d7234b3044df6c680',
    ),
    (1, 7, 3, 0): dict(
        candidate='e5af9a72cce985537f83839541cf09847649f1053eae0d4864d8bdac79d49192',
        book_values='c3873c288b31b0c77f87c4db7479ef53830eade58b09903bf911cc5a23eedd7c',
        validation='6d67b4d9c2ace3cc5f883762ef7deeff5b3594768e6e524324d9eae263587d35',
        comparison='e3e4c3f708ec8ff9c897b226dccc2abe7e92e7011094e723665cd584695adcfb',
        layout='a7a309af2be5c4337476c01027237566ace96846ad4d5c73e255e039f176f9e5',
    ),
    (1, 7, 4, 0): dict(
        candidate='d5bcedbe06db706124ec6ea7f32e45902216657f883dc49e149d14f347fb1cb8',
        book_values='8b633a81bc512da9fde9ccb4580a450bf3e6a596a6de28eca25fcb2afb4a697e',
        validation='c8128da20e8a5915a9eb56a2cf9294e54c51e0f32c3f04ce9397090ed92efffb',
        comparison='280ede1aaed868e92263feacf27bc15d833d8ce1d816889b28f74ac6ef10c694',
        layout=None,
    ),
    (1, 7, 4, 1): dict(
        candidate='6dd829f62b10807b08ab192f373a187f81587121172cecc0b31247a8ba8d5ce8',
        book_values='3a131275cb5543d20ac9d4baebcce1500465fa14d9f2997d3ce36e21a2c4315c',
        validation='1b9990c0a4112dc428db49e92588c9090d5d17ef19587210913fb60fdb3d101a',
        comparison='dfaa63ad344cd2c4ff4fbe43d22173ed4c448029f1f249a71b6c6d98db06027a',
        layout=None,
    ),
    (1, 7, 4, 2): dict(
        candidate='f65cc09407a1475c8f87aea848a1ab6ce7310300740d36664ca53617594408ad',
        book_values='ad29871886d129221cf7d07d3b06a8677eaf3b64fff1a65eff12eca37f445868',
        validation='be77a89029a49d28370484859723e4438e1f953cb7d8419f55b901acdf2289cd',
        comparison='3b131a2f2fa0704299e7cae7aaea7deb78f206375086f0580d7556f45ab23ae2',
        layout=None,
    ),
    (1, 7, 4, 3): dict(
        candidate='ece650dd1963f79c4785712b1977e80a2160940845116df7fa12b483f42be5da',
        book_values='cb47d41153fc0270f62fefca81d5a6f6a88dce983b091f62e4175d5aa61d73c2',
        validation='a576bc2ff041d0f74ba538757676db7553a7b95a0743af9822c8a8d60910580e',
        comparison='6d40ebd035f9da0181b82f12dcc64e930125f3456721954a6f56c8e71e1d579b',
        layout=None,
    ),
    (1, 8, 1, 0): dict(
        candidate='b91e94f4b952ccfda2a659c52bbee7d9c496a5fd928056ccf789076124a77af3',
        book_values='a955572b33dde47f02187e6f90937107f7ffc5dec92782028bd3d3ae92ebd5de',
        validation='08570068d0b52d47072e78dbf7c1fb8c17ab78cc0f0af40187b16f50594cb1a9',
        comparison='8785fb821b8cdd6057e153bb0ff26a2f025fe30aaf2ff8b63ef1b9087ad9aa3b',
        layout=None,
    ),
    (1, 8, 2, 0): dict(
        candidate='16281d72740806e001282f0bdcdcd354f7fa0992f5d2d7fdb30dc098d9e904b6',
        book_values='619acbdfbd1c7304f176d0bfb00e95877a6fa7a8a6f255ade85b503c4b7ed5a3',
        validation='245654b8ce6d878cf96e3f9909c70dd1516b72d3d242a760188da06062ce3e3d',
        comparison='716f0603517605ea3f46f2b6a2c581fad8893f648ba93262a5b5dccbb18a6579',
        layout='97c89ee1988df672fc523d652f0043f3089209ee4fde976c108002a6fe576a0e',
    ),
    (1, 8, 2, 1): dict(
        candidate='68448cfff9cdf395c6ec62423700883791499c1a9e0820513f6681b3073fc423',
        book_values='ed81f9cfced9a6fb38b45e51a9ce906002c4f73d26462d50fd9648191e7b27a4',
        validation='a15fc669aeac7c31f5090b57a9ba76619af02f6541a62d39437553857a997484',
        comparison='8efc9da274cf237612f2f456e3dc8fde0f09dd303fd35f89267e61a7d1fe60e5',
        layout='97c89ee1988df672fc523d652f0043f3089209ee4fde976c108002a6fe576a0e',
    ),
    (1, 8, 3, 0): dict(
        candidate='caadbba9399b63c8e16eea7442dbcf8e0c3fafa97f817b9d416c5e875962fcc1',
        book_values='d459ec014b6e80d580f98b3f21e39ed511ba0c00caf75a8216546ae8d84e521f',
        validation='8c58d8a11192fe64a26b4ea695107259547c8a7db2788dab5a4b90048773ff92',
        comparison='c77ef480fec7a22d5edf6f38d20c5cfc17a9bf8decd3eb38954f3e0e4e79849a',
        layout='c91167fb93c0727280254c00dfe1497aac38434e63e0408fd93a25cef3780540',
    ),
    (1, 8, 4, 0): dict(
        candidate='20316d897ba5d818d1419dcb6af79ea5ec3e965551d5755eae9d3a47281b7ef3',
        book_values='386f0a365f2b126c21cb5cb65249516ea0ca41c7f0edb1b8343dd06bd07da0ea',
        validation='2ff637c10202bc68d58983e229e3cf6025dc93dccbc0e8b20233fe6929fe306f',
        comparison='fb2e613656b171ec25ec7f6614e62efd195dc309cf466a3c455085196f70a814',
        layout=None,
    ),
    (1, 8, 4, 1): dict(
        candidate='643bcfb5995cc9cf8d6e8f15596c2f4bce8a0c187139ef72cfb21cade00c8450',
        book_values='ffc342833298a000534526b6dd1bdd919396cc24e67b6c6032ffbb443633b0e1',
        validation='ff91196cc13137d59eccde6f8a8435d710529b0228e8bea8b74cc0aaba2ee4c1',
        comparison='89e6605728ca3a35ecb1a8aeaaa8c5d13a3cee7e5a99a282161697ac6db98f08',
        layout=None,
    ),
    (1, 8, 4, 2): dict(
        candidate='f29eccfa0fa54eff03b52be4f95bd1f7846ea5a248411ff55ccaf7245190a339',
        book_values='03b71373c86f717def45d7324f248addd70cf2ec2e7085a176cc7ef37fa4898a',
        validation='e374fc89498e24d06df62df926d051d30ce8d9f556fb836c0dddd2fb07eddbe9',
        comparison='bbf8be8c344a7424d9204554dce767784726faee3c24a76c4083ecb5797e230d',
        layout=None,
    ),
    (1, 8, 4, 3): dict(
        candidate='d3a5f1114e528f921c706e1bbaf0cc3a549f7634a5b04a71524ee78d4f159972',
        book_values='2cafaf96594c338f28a1a8987e7bdce12002d391aa327ab3ffeabf4977f7e238',
        validation='14bcc1105a4b4f9ffd1e42adb075f667d5e09134796b3c89bf99df014c9cbbc9',
        comparison='6b78362b0f74e1da06581c18bfc737bd60bb071b51351ec317909c6d35a6bc02',
        layout=None,
    ),
    (1, 9, 1, 0): dict(
        candidate='b5085b81cc74d73d9fe410bb3ff92354e1494199ab72062de6d178ba6bd9f037',
        book_values='290cba1881d2d20262fb744c2dd58591e15cce79de44e165e97d744a5e5aa95f',
        validation='08306853268faf70367e8e93c411e73dbe5c0c317e047478931008f8b1112dca',
        comparison='06739aaf1a9796f6f24cc8b25e427af988f7f74a054d1e04f7ae7edd10fd3aa6',
        layout=None,
    ),
    (1, 9, 2, 0): dict(
        candidate='b28912f3488281f385ed975d576d8fca03613a32c08d988a1e272e22e25ef825',
        book_values='574dc21fe395748ae1aaa9c05e0df5d0f51c6c1e679ca373925b169539b26792',
        validation='9b59f76164d358ff684eea6a9dd3f02d46363a740ff51a1937b61185923fdf0d',
        comparison='00080961f6050cca030a00a79226be903a85a143b68157d81589ac0f81b116da',
        layout='e235da34b4daf151a9a449ebbe84a3d7f506a2c68c2ab6c7061e0eef3c0f6e1d',
    ),
    (1, 9, 2, 1): dict(
        candidate='78ad15d6976712bd52098408aafa031b5538e5f28892c7bf753ab36ba840f066',
        book_values='43d6fd01a0aa14960b01c1546a183186797b1877f7bc25def955aeb96fb3d662',
        validation='8aff4b25b1017aa06e1595e48351fa813f34775b8510f552b046f86cfa4a5e1e',
        comparison='80998d75526bb6e2334203bfe68715fe859169622adb6d68fa1091cf91da12a9',
        layout='e235da34b4daf151a9a449ebbe84a3d7f506a2c68c2ab6c7061e0eef3c0f6e1d',
    ),
    (1, 9, 3, 0): dict(
        candidate='179156d8b3f98c021ac9e1e4ceb72bfd27d6ced5ffc814620f25dfaa5dafed5e',
        book_values='d6d7c232a479d09285dc3c39fbe52da118a6bdb1518942b52e19341e5b585bd1',
        validation='17de5222dd07eed7d95d9982181ab6925649f7517d0f6189731f3dfce82ff1a0',
        comparison='c83031c551a5cfba677e4c2ffaac3dfbedf00faf06876e95f60967a09c4ab979',
        layout='acf0c3d0e4b2e9176be5573c3262ee8f697069aa35275f4ce0fa58c30497946e',
    ),
    (1, 9, 4, 0): dict(
        candidate='cb4b5161ca72ddbee7c3da335b34155aed56af595ab5e15f057edf8e5f9d3ac5',
        book_values='89cf9a0994c9e59e867edfc2445382cd0af5d5ba4d9545e0fed66c658404ed25',
        validation='ee9b0582649472401daff3a29c17a06aaa0ab2a5cc738587316f89e78c11292b',
        comparison='721c28dcd610efeee0a59f9c09fc55d86f2f5d894252a9c891f39870b214187d',
        layout=None,
    ),
    (1, 9, 4, 1): dict(
        candidate='721ba1ae6ff48549cea0010219736d8665e656efe4f96c4df2f264fd78674f2a',
        book_values='b4d3389d0d77618d85a71e7d908c4e77c3a78e41b24255d95e99c8d872b40ece',
        validation='edc734d4c6362b09cc9cd55b265ecf512226c333836a54fc2627d3576efb4cf3',
        comparison='88ac877dda38ba7f50188f5686cfe8fbb6ca878f301fe743b5b277c9674ba8d4',
        layout=None,
    ),
    (1, 9, 4, 2): dict(
        candidate='72aca659487aa3362a7012a1218349ecad42bd49a96e5e38ff71edab30998f31',
        book_values='3a4ce49b7eb0c77bc8a34dd003c0ecca4b1a3b0dd8096aca4949891331740fe3',
        validation='6d16da1a477c2db5d3c1b0bc4193db33a57cef0c72930a51f97b26ca94673690',
        comparison='6980f36baaf9d21e1466bd4463c6d92d7ed1bd836e59cb0f2665ed19de99e42d',
        layout=None,
    ),
    (1, 9, 4, 3): dict(
        candidate='2f01d8b0c1b54c12bf8d89ff9273b3599d3d4a0aa9a1004e2fd0a891c0c3f35b',
        book_values='e0f6cfb678c3b8d161296d270b017912d9a8341a946863ba74e1c694fdbb1752',
        validation='c5e81af5c966a5fa9cc5516350abbdc5d094d7ad7744243f13d5fb640e670659',
        comparison='ad4d5c0933ff20044f5f2c7925d5f29e65b04bd39d90a06cfb02bbeed1b79cc8',
        layout=None,
    ),
    (2, 6, 1, 0): dict(
        candidate='f77a1f0d7cb0745bc0b4ce7bd33c8ef576c3354cdffa021bf6ce1cd6b2736f72',
        book_values='61ac022e728bf42bb0fe5be3cef03d8e5df478a6845e51ba516404ba5784a7c4',
        validation='da29f4eb7b5ac2d0a30e78102d9cde1137db5bfb37b150334ef3c08dd9be0700',
        comparison='484abf792d8038a906f473f5a306535151e583e55ab3800c82a441356ee614d0',
        layout=None,
    ),
    (2, 6, 2, 0): dict(
        candidate='5bf4f39a635602c8f3b5bd611fe51317d9b1ee78045e9186e45246f66850f05c',
        book_values='0085766d04a612d52b287b2ea4e2c3fb68a80cd931786392add2ef66d4385d2c',
        validation='fb01a249b26baccb0587ca8c0b44a86c6405a1c7dad83c7886dd8901b5e5df1d',
        comparison='e3dbd9fd493a75045b756cee39ac624abbccd5b37c35cc798f81b345319f6185',
        layout='75ad60d44364cfeb217611139006cccf49cf834eac248712679368985cc892d2',
        group_values='4b55519b2d83828d2a2d87816db3fbdc9eba1585e2f81ed985e24854ee1dab20',
    ),
    (2, 6, 2, 1): dict(
        candidate='b8790c3e4a59bf696286961a16a9174dee71868b5fc22fbf5394a649abc4aeab',
        book_values='f875fe9494d9ee82d186411bea569270003856b4299ad31d5815182205fa5cbb',
        validation='038898f83bff71c6a975039d8fb4a92e5d3555a17003a36f45949df0a6d70b84',
        comparison='f3a2384019f8f485ceba81dbb3d41cd4ed32ec71622630450a48e033f2d21ae9',
        layout='75ad60d44364cfeb217611139006cccf49cf834eac248712679368985cc892d2',
        group_values='4618e992c6554acdfa809f80c21c4a5981b9a2fa07851c89ec4c1249212df290',
    ),
    (2, 6, 3, 0): dict(
        candidate='db1ec408451b09fb59c8b40fb7e062cf48536d4e517319ee8ac5604990b45646',
        book_values='ffbac9eeb00db40476c5f92d80fce4132dc19b4d9273a51c15d8a3bde5a764ac',
        validation='a52f2dc5c0519e26ce6e80f263b3fbd78ae1ecffe7e0ca038815e2fee070e8e4',
        comparison='07755597a5e1483a719b76d3e67fe89ab12dfd04b70db31474b83ea61e1d3fb1',
        layout='305313e7f8f3211f227a5b46044c81ddd0a32211f2fe8b7c72912e7b0dab838b',
        group_values='5cfc4211e4918b07c75c1ec51a90d9f5423607c3fb588a14a3bd8d0d415f8886',
    ),
    (2, 6, 4, 0): dict(
        candidate='8497a7b28a2fa0d2dc51bd69ff4208f0f304eb3568dd117b3713836141c73711',
        book_values='e571aae15614927a3f417820ec13395889a68d8911648ada7b6848848651ed88',
        validation='4368b7df62ee2fcc15f2d0c54f3926d7a4b243d06f7424feb69ffcd470fc9f59',
        comparison='d2bb507abb4daf6ef1336a04a869f848cfcfaad88a738c988dafc9e5df99ff01',
        layout=None,
    ),
    (2, 6, 4, 1): dict(
        candidate='b2a2919fad91335aad4c765372855d7742cda0ed6ebd94f6fe2013bb03845bf3',
        book_values='2bd4b7a790090886142d43964dbc6aa8d32a0992c4aa5429177d58a560c87e52',
        validation='ff8ff219cc8881e5a28342b13e16de9e96975467cfae137c64a30962e69f861e',
        comparison='a9c71b989bb82051750adbc02d009f3d4bda130e11106eff4a70421c6081c754',
        layout=None,
    ),
    (2, 6, 4, 2): dict(
        candidate='c34638f0f9a3fac5a969cd8d3fe5ffd4ee7085c1c33986bb84c76c433c62b229',
        book_values='34c6397a48e22876ee32f331f86c696f5b7ef41a717d20ef5312c93bea2bbea5',
        validation='d4df46606c8758a7f97f35fde8303eef009ab908cb5f9bc3b29e04b8a13787e9',
        comparison='dbd0e02de831a570b9a105aaa460986d57fd8f0216390e490299954dcd569b86',
        layout=None,
    ),
    (2, 6, 4, 3): dict(
        candidate='709beaa2fca6b22027fbd93a8df011daa4c51b9f09a11de9f64fef606c36978e',
        book_values='a3553af20156c67c5445f9a139707f8e4874ac26f506308198068687836ba6e7',
        validation='0ee1ca4b03306217a2a37ad85d8d3125e6ea8b43503b6694132559e3cdddc06a',
        comparison='142b4b15a31e55e94d22f19fb590c1b6820a6b6af1e13c1e32a978c2f483c93a',
        layout=None,
    ),
    (2, 7, 1, 0): dict(
        candidate='632833bfc6957fe8375c095ec8e1c2bb9cde3ba4477fb569af360a30dbb70532',
        book_values='dc83a39fd7303ba78a02aaa7fca394452771afe64bde9c94c70f2ed257231a9b',
        validation='f7ed068e2ffbb4aaeee51b4215b59011581219ad9a339245f5b7dd13b56f6323',
        comparison='4afe5e97c5ed3cc3d54b76cbe90db734689425813e501c4a2f0de06f413ae9a6',
        layout=None,
    ),
    (2, 7, 2, 0): dict(
        candidate='ed28c2c783049bc0b56bbbc736e2871dd956c00aac90a25dd6a95c92d288fe2c',
        book_values='07cad56888088201fc57c0d276f716413b90caf9c421b88aca3220f97b5c771c',
        validation='5a9c11f9ba29b0bce3bca83c1ab2f7804d305184734b86aa2d94435cc2d0235b',
        comparison='f4efe9c42c61f4f4ca5ac408e6615d281395fd0fe119ece5bd827334f6853e01',
        layout='494c7708ac7d2e35cb5950de12133594d497ab8e1016a69ba4bb19763f700ce8',
    ),
    (2, 7, 2, 1): dict(
        candidate='e1511d6c37b902ef79df76c3b629f0632cfc9575aecc66f08c6311a97f5db979',
        book_values='de8bca84622030b977de2300aa3f586d4792c65c1483eb66345072e082310d10',
        validation='7b821099d098cf5f23b926a4aa44c86efc92f88608eacf72ede2532537b19288',
        comparison='c17f68322676d97eb4e564fcaf929bbe4b92dd5e570b693eff5dd9beb4e0466f',
        layout='494c7708ac7d2e35cb5950de12133594d497ab8e1016a69ba4bb19763f700ce8',
    ),
    (2, 7, 3, 0): dict(
        candidate='87eb360a42ff29bfc6379ea856e7105ccf23717b910b92c1f4b05a784c4afdca',
        book_values='cb3baee3cdec8298abd8f60b702cc32789efc28d9f2acdd4af69bce0f04f596d',
        validation='bf84e78085fa2381c4d37691d359265d2e936b739e5f42b585a74c57f3e7e0c4',
        comparison='7f57cc172ca5d8c2d90b985fc4594ef9bf6ce3ed8b637a3db837fa6b708775bf',
        layout='cb27daed39363d9b6290156c06b6a100c46a833b0d030cb1df9f33a8dd0332d3',
    ),
    (2, 7, 4, 0): dict(
        candidate='d8c28ab29004633e6b5d30f1a679c22053fbe83332a1c5a3e8d93512c9872268',
        book_values='e15962d8626d5a5a1184f2f389795d65e857b674cb9f17b551a01cc3c6069086',
        validation='a13c57940684b09a2d9514221c37c2f295b405ef05c40b7c5d13181a574a0813',
        comparison='7a5d33b5cedfbf4decd16115377787e36d69d51b4fcda05814ea16bfaa313240',
        layout=None,
    ),
    (2, 7, 4, 1): dict(
        candidate='3460d1d1e6fd90bc502f082de48d547a3c8a2c64d9ca1df7cbf5016bb2d6b953',
        book_values='9bed0200bed2b18906aec8ac9ebcbab8a422ee9024a56fab906723cb9bb7cf39',
        validation='758544835c326fd244b24be58f3b6810c4a9376d43406fc5dffea2b85287ee15',
        comparison='3612abb9a8ac24690a0e5338e1480589a9107ff2b39b6d4464b5592b8dbea6b4',
        layout=None,
    ),
    (2, 7, 4, 2): dict(
        candidate='3a8c3e1f2bb76b88d57d09d0eb91a46e2b6aeaa86f95c66e4146a2589681957f',
        book_values='19b5e1c80034013f7efff6e7c98a7966fc3381260b92be6837a97a362d2a1244',
        validation='2454aa0faea4927f70b1f400c82542bbb90676ad80fc06fb2704e9fd107619d8',
        comparison='e5f96a58d48518c10e65289308a476290879e92b6a98b7a32f56774b736fdb64',
        layout=None,
    ),
    (2, 7, 4, 3): dict(
        candidate='4c66c8cab6a430e680d29b9832dc343f2422a8423d5f39c17465988807ce526e',
        book_values='306c0f3f7026cd28d5a9a5f4e349720776eb85875c468fe3f981a3e6dc1db439',
        validation='97cdfee98b1faad950d1eb849ac845060deecc2092a102f2b6d618f91ac66cb3',
        comparison='2ca19d44a6117ea0950936f4299fd052075a5f0acdfacaa5b72078204415f8ff',
        layout=None,
    ),
    (2, 8, 1, 0): dict(
        candidate='af41012d6819f59246813c4ffac961b2c802359ef2b856323c691f22a79a413d',
        book_values='64af5b2598a624793f4bc4f33448f8b0f700eb3c6645382e9307bde031af28f2',
        validation='33d59a55365b55eb0a53d0369f1e55cae58e5b9667c018a91c2585cc5dcac184',
        comparison='78571756625d6fc6b3f6ee52ab2b9dd64a361f4def072a6b9ddba88a9e8fdab0',
        layout=None,
    ),
    (2, 8, 2, 0): dict(
        candidate='86c6e1928714797c62a57a7261b8160a2926f5079625c6843278749637e58b42',
        book_values='c0ac71a3209d2c11fec50b9ce6b3d32d10d58823ecb27978ce42466966012220',
        validation='d9bf6a2ae21126058482a0b394f34889d0f5deafea0a32ee07452f50392e5097',
        comparison='bf57ac614a8de72554313298621bbcf583192939d52758c7378d0aa91eb8bcbd',
        layout='101c553a1fcdab31beb75b4b8ccf0f7a0a26cb8e1dc5ea77917a6867fb629883',
    ),
    (2, 8, 2, 1): dict(
        candidate='f70477fb02a613b3672de85f9bc6d7517c205d1e9ab8aa288bf9199123884cfc',
        book_values='96ef473c65477f0abd25b6dc724d2f5d48e46cf9cc4f681cf4e794bcf7acfd1d',
        validation='d8c0bd1a60c9eedc4350e08afd78bb6ab835bc24a977455ceac3299f59606fe3',
        comparison='90f6f04c2358785be0477fa0feb745e1909f5a89403081ac989847637b70fa30',
        layout='101c553a1fcdab31beb75b4b8ccf0f7a0a26cb8e1dc5ea77917a6867fb629883',
    ),
    (2, 8, 3, 0): dict(
        candidate='6b3fcdfae538d362be804d6d9177b4b669a4ab0cf1d22263c89f81f3357d6ab5',
        book_values='50a86d4ac99c012c1155fae5f7e7c129635b183a4733380581254f68ab862386',
        validation='3415afde05059a7bbe2d8fa8ed6a59321fdb4c2b3f734ffb61430ba5be9f14ef',
        comparison='598de049458d7b0cb677ad4a426535c887cc7df2065cf1eabc959496c2abf040',
        layout='03ab542108773b777bece1fcf1b53701b8745c353cd17d596aaaf8472a60d4b2',
    ),
    (2, 8, 4, 0): dict(
        candidate='e05ae00166b8a95ecdb4b2ce9ebcbc09bd7f575b6d04cab0494c53005629eced',
        book_values='7511940f55ddf69a8fd41abeb1fecfcce59bcd114ff3b24b95f798bb861f37d7',
        validation='b22fa050be396ac95d3cf3102237edc2da7945986568034d5609d36ab5c71b0a',
        comparison='b4427e64b16b85960b313e8763d9c0b004a9c0c56ef7de1430e2ddae7d972359',
        layout=None,
    ),
    (2, 8, 4, 1): dict(
        candidate='3ab632073d320a385bef38395e3512346088023bb726d8cc6c28cde1f537cd86',
        book_values='da330e108606ae57414782545940304d3d7cdcd40bf61c5281b28d7a11f75dd3',
        validation='bf31f7a1cfe42fb9814739ab62c6ce71aa5699703b427d3ca49b7716f4d6716e',
        comparison='5148601dfd2af435ed0a7ab40b6951084dc9fbefb1faffa41ac3b6502254cd27',
        layout=None,
    ),
    (2, 8, 4, 2): dict(
        candidate='88d7d5fb39b2bcc3c8ae29f1ad7eb51b92085e20edd5798a0e919361544f93cd',
        book_values='924130045265da3b1d2ae542f7468f87e187d2b3db92941a46efaaf05b511b34',
        validation='151e16a94224b0bad12f47c7233e6190970c6c0787330e2c3ecc34143d9b2ba4',
        comparison='4eeeebb516b3720f68e0c2a4fdaf3c8105806dc803702dc96714870eef2ec9ca',
        layout=None,
    ),
    (2, 8, 4, 3): dict(
        candidate='c6401813190e84c2e20aa254586742965b6162733993fe330ac79be999f625d7',
        book_values='a65b440dbb8cc88497cc6f0076fe9bd0d82f5132e269df2557d8be8157c189ea',
        validation='6b1c1fc539cad94e17b577585b195aec3dddc81f44e67a6bf84a12b8aaeaa47c',
        comparison='c16e0abff1c4ab9ee58e7dfbb0a9766c954b9be4187d3f7be3741e8a6afdfadb',
        layout=None,
    ),
    (2, 9, 1, 0): dict(
        candidate='6949fc2916e3b5b6a7019952832f194e88b60fa9246290c6568a706f2593c16e',
        book_values='9695777eeb5a8ebb57a8c4cda538ddc5a2320b613c7dbae5a12240a2814314b2',
        validation='8a676e9fe866a285e79e0aa75e542c0f97f108e594dbc462079630a32820940b',
        comparison='ee96fb42176dccc707c0f8e7303925c9eae19e9769df0f55e91dc8adb8d358cc',
        layout=None,
    ),
    (2, 9, 2, 0): dict(
        candidate='e4fb0f979c8c3bb4ea6f5bac6b1cfdc21c766861490293b2763d351675dd03f7',
        book_values='e7488096c0d8e2434c36acaa45ee27f8e9e52f2d225a0d961d512c0c0319f284',
        validation='bbfec6bcf66aabb9c05951dcb24d853a2e23e596d03f8e34c9273dcd500cea03',
        comparison='9d7b3162110d512473da0e4552cbd441fa2d6b40c9b2dc47f9a376a1f180896b',
        layout='101c553a1fcdab31beb75b4b8ccf0f7a0a26cb8e1dc5ea77917a6867fb629883',
    ),
    (2, 9, 2, 1): dict(
        candidate='dec71ffa48fd45c8550d89743214d85a819f009669b43342a3ebd7875c3e9091',
        book_values='3bcb2439a0ad4cfb70ec55efd1b11d6ecffd16dcbf0d735be2b196916c9fa52a',
        validation='d8885d52ab01aa304d5eeecebd496482d09dcda09987cd5081daca4ef462e7ff',
        comparison='5cb4def2e6085c4c0b1c6e1b1153efee91cb541d082778dbfed1fe6c0d1edea8',
        layout='101c553a1fcdab31beb75b4b8ccf0f7a0a26cb8e1dc5ea77917a6867fb629883',
    ),
    (2, 9, 3, 0): dict(
        candidate='d7be7d964c87a65699849e57f5960187a3a59ced636999422b2fa6e5ff5e3f7f',
        book_values='a43174c2ff45a38843858175c8faf657b60e8de1441550b86929873b71d0898f',
        validation='3b77c474763709e4eba12e0782ca374083995545e12c6c6013e6858f4a59a1c8',
        comparison='1c5350d8f629c58b08aa38b5f427f63087255524a5ff5eccbdf4bc7c161a2866',
        layout='cf69f63c3fdcef43002db9c2b812f7a9b34fa764379980a84ca21b29884531c1',
    ),
    (2, 9, 4, 0): dict(
        candidate='c374cd664893418dce0e52bac4d48fe0cc0e835a498b1b947ef8c4e388b8a9e1',
        book_values='c1ec5994596fab0f9abf79831f4917b2629ce85e5926f9ae82b4b88fab29fc3b',
        validation='ec006159d3ae339f070dde0ed855b5f96ae73c307b4fc5d13d378b9eb270cdd1',
        comparison='83b8f11ab0f54866a81b70135b326eb9395bb774735b592df200e4b37ab42c0d',
        layout=None,
    ),
    (2, 9, 4, 1): dict(
        candidate='adfaa2f9abb41f04307ff5fa6f2c4d182e0d6b855ed8babdca119ed593b47a38',
        book_values='73495d7a43f9ef900746b40cad44cfc65a1bf9793e662d72bac5c92b96a6b085',
        validation='1786288d31d6d732de6988a0c7696df8413129d597a8d7195b0d09584b55499d',
        comparison='f0ae87c0cc583f340e3ff66817fa991435e87d7c28b500727a519c8af445b385',
        layout=None,
    ),
    (2, 9, 4, 2): dict(
        candidate='578d0d959962ee965077ded8fff38a5dafc1d5d74325f5cf622f9db348ee21c3',
        book_values='e1b1809763b35b6a76633ba8231301e1cfe3c944d7e7cf86c534dcbb7f305dbd',
        validation='f195cd51c87e900eef8fe4b78bae31c1c50afa81c908a515862fe7f7dd4ec799',
        comparison='7ea4c5db9492964358b2cc92fa980fb576ac631601e7f36d700606216bca50b1',
        layout=None,
    ),
    (2, 9, 4, 3): dict(
        candidate='80c5df360e57d17cca5659926e77e30d7d6c014bb9b94e50bb1691804a7b52ca',
        book_values='90db4692d19c3ddf4ef0c93a3b6a4c06a18e7221e467534e37fe4a82f2206984',
        validation='cd81d10f73984fa16d466eb3ec1af6ede13264122424068632ef66f493db2bf5',
        comparison='76587e7f06df8eea2ee65abcc4e0ade264934a54ad83875ec6809b4a8cf1f067',
        layout=None,
    ),
}

LOWER_MATRICES = {
    (1, 0): dict(
        candidate='fb761c58c15d8a17f077a8240d1660512bc4f4b802433607238065e2364f2c14',
        entries=16,
        matrix_values='9e206890d7fba6f91292d7de1e1e90f5a48eb4313d06ab187cfeb504987c09cd',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='53694f231d7bdb77b26f509116f89adf6bc41b44760018c3275800deddbc68b3',
        validation='76c467bcb2224d2619684fe33ae87efe55dc87c167a65f7d4ccecd24bbf78e03',
        comparison='5b2460b7ab665b815a7e107a5497ee25a0908e8aa40905a9c205373957601145',
        checks=182,
    ),
    (1, 1): dict(
        candidate='764c4782d2a9d29d8d86eae4dd0f57b233911acf44f90c920ae7d3c2519e42cb',
        entries=16,
        matrix_values='037b8c69a464f6214b4c12162de2c96a6a594cad73894d2f8db2f190d0099357',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='7f9ee36d40d4e5ad38226c00270e9d01b09cf16a5b3089d27f15b977d845b3d5',
        validation='0f146dcfe3fc801148ce67eb84564d5674631f1db02f55d99b8b64fa9f493d1f',
        comparison='074b7b33280a7865056107af9a4430df17d3bfb649bdb046afe5f7d4135ed638',
        checks=182,
    ),
    (1, 2): dict(
        candidate='7e832da7fe66f51e0178cdabf81915f3e606bb0600bfd53d71a913f1c8ded605',
        entries=16,
        matrix_values='fa20cf83c2a7adda7c473d3e7c3c404ed016778489e138705a7b4a064b921ce1',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='1e01dc860545745d087664910218aed97431fc67cdac7abca7db204b4f26884c',
        validation='8e4fb2d73e4944f83e86d8511a664ca55c9262cf2ee7bbae592abf4d07323a18',
        comparison='7a899cb9a6484ebdf20144ec5ddd9717a94ac78129a205dff52f8325f73474eb',
        checks=182,
    ),
    (1, 3): dict(
        candidate='98a14ac0538f69de9a0d510e5d67f68b76328572fcbfd77066f385f6274b633c',
        entries=16,
        matrix_values='465e963ebbf3fe865fb7a24fb196ede70f4cdfa8a8abb584b3de1005b1dbf17a',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='164ef917a799f43dcc57a0e21dc1b506d53b7926fb646a619facdaeceb2f8785',
        validation='b462f21ff9f0f51fa44e0d6fb6672a10ff0f658f86ebc12e90354b7cd6da2823',
        comparison='28ca1caa8eb84af0cf2ecd7b2cbb1fd93e1f6501e5f761ce86a5d005f7f162e5',
        checks=182,
    ),
    (2, 0): dict(
        candidate='7d4bcd010f99803d373d611de36faae5968752f803b9fcec35e876821727346a',
        entries=81,
        matrix_values='6badf9183a38882b5cf270f33e0eb62c0a84d56319f4eb3b2b1f2a373e533f7c',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='8497a7b28a2fa0d2dc51bd69ff4208f0f304eb3568dd117b3713836141c73711',
        validation='4368b7df62ee2fcc15f2d0c54f3926d7a4b243d06f7424feb69ffcd470fc9f59',
        comparison='d2bb507abb4daf6ef1336a04a869f848cfcfaad88a738c988dafc9e5df99ff01',
        checks=212,
    ),
    (2, 1): dict(
        candidate='7f67c92a61b00bf06b5de063c5905e3773709b13856d285ba74428093c3819f9',
        entries=81,
        matrix_values='1afe1cfff0caede3a61c81606172b705984ed84f087667386d578c52b672527a',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='b2a2919fad91335aad4c765372855d7742cda0ed6ebd94f6fe2013bb03845bf3',
        validation='ff8ff219cc8881e5a28342b13e16de9e96975467cfae137c64a30962e69f861e',
        comparison='a9c71b989bb82051750adbc02d009f3d4bda130e11106eff4a70421c6081c754',
        checks=212,
    ),
    (2, 2): dict(
        candidate='3fb2c95cda2a0047e94d503e8d6b92f4b68b6ae44247457998c4a2f8342b57cb',
        entries=81,
        matrix_values='971f3b565dde01552f09a19c4ce14e15e6c3ef094e9a2ab4d76a0639d3ac5867',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='c34638f0f9a3fac5a969cd8d3fe5ffd4ee7085c1c33986bb84c76c433c62b229',
        validation='d4df46606c8758a7f97f35fde8303eef009ab908cb5f9bc3b29e04b8a13787e9',
        comparison='dbd0e02de831a570b9a105aaa460986d57fd8f0216390e490299954dcd569b86',
        checks=212,
    ),
    (2, 3): dict(
        candidate='90b3c77727177928fa823bea9622ab2f275dbb445ba1aef3356ca634cdd5cb8e',
        entries=81,
        matrix_values='6d2e918ee22adcb88c36b13462638f80be319bcafeb1caf5b25c33632790d473',
        max_half_width=2.3251493876046068e-07,
        codebook_candidate='709beaa2fca6b22027fbd93a8df011daa4c51b9f09a11de9f64fef606c36978e',
        validation='0ee1ca4b03306217a2a37ad85d8d3125e6ea8b43503b6694132559e3cdddc06a',
        comparison='142b4b15a31e55e94d22f19fb590c1b6820a6b6af1e13c1e32a978c2f483c93a',
        checks=212,
    ),
}

LOWER_BATCH_SOURCES = {
    (1, 6): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='0d07eacfcc3f4ff0e9131268a0a2147ad418c5b6203b8c509399a9d64de88c29',
        qualified_prior_sha256=None,
    ),
    (1, 7): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='ab2470660285d674d60700969cf1924e6a88fc2c040a4dd0c0e8636af77fa63c',
        qualified_prior_sha256='02c989bad8c8fdd6f2f35e44410c536a886f51ebf21da33f2036bf23247a0bec',
    ),
    (1, 8): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='a831d198f0f8b2dcf5018701fb423508ce53fca11b80e4252140f001c8503914',
        qualified_prior_sha256='02c989bad8c8fdd6f2f35e44410c536a886f51ebf21da33f2036bf23247a0bec',
    ),
    (1, 9): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='cf99a946f0872c6e1a6ad2b98a3f6f45bd09a6474428790dbbef2f09c0ab1995',
        qualified_prior_sha256='02c989bad8c8fdd6f2f35e44410c536a886f51ebf21da33f2036bf23247a0bec',
    ),
    (2, 6): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='eaaa2402e63ab8b7c1b064c8fed9caf99a0b11ae597811ec3b0c41a9358b0680',
        qualified_prior_sha256=None,
    ),
    (2, 7): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='1a19e1fe85290d6987a950be756f5aeea75086646f740df8623d2e7c41cb021b',
        qualified_prior_sha256='33178f5016ba0abbb9113854953eadf72be66553f6b82d20842c527ad73d0e1c',
    ),
    (2, 8): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='c79ae8b0491fdcb8d8f2152ddec2a90bcb0d3d3c3591a677a68af5628ac7e300',
        qualified_prior_sha256='33178f5016ba0abbb9113854953eadf72be66553f6b82d20842c527ad73d0e1c',
    ),
    (2, 9): dict(BATCH_SOURCE,
        experiment='hoa-blackbox-orders1-2-v1',
        code_commit='d47255bf679348ce9b9441bd451bab3f2067abbe',
        tool_fingerprint='0211471ae8286300bbb8909c74e0db2b6247bedd2fc51268f14e351109120b24',
        analysis_policy='hoa-blackbox-orders1-3-q6-q9-joint-mode2-v6',
        policy_sha256='709253e5f9f4959c2d0661f131fd29420f3713d2b3fbaba7c9b0128e03c4e4d9',
        qualified_prior_sha256='33178f5016ba0abbb9113854953eadf72be66553f6b82d20842c527ad73d0e1c',
    ),
}

def lower_source(key, kind='codebook'):
    if kind == 'matrix':
        order, cluster = key
        record = LOWER_MATRICES[key]
        source = dict(LOWER_BATCH_SOURCES[order, 6])
        source.pop('qualified_prior_sha256', None)
        source.update(method='public AudioConverter PCM reconstruction with frozen calibration weights',
                      measurement_quantization_bits=6, candidate_sha256=record['candidate'],
                      validation_sha256=record['validation'], comparison_sha256=record['comparison'],
                      max_empirical_half_width=record['max_half_width'], empirical_half_width_limit=2.5e-7,
                      held_out_checks=record['checks'])
        return source
    if kind != 'codebook':
        raise ValueError('unknown lower-order measurement kind')
    order, precision, mode, book = key
    record = LOWER_OUTPUTS[key]
    source = dict(LOWER_BATCH_SOURCES[order, precision])
    prior = source.pop('qualified_prior_sha256', None)
    source.update(method='public AudioConverter PCM black-box reconstruction',
                  candidate_sha256=record['candidate'], validation_sha256=record['validation'],
                  comparison_sha256=record['comparison'], validation_scope='Huffman codewords and lengths')
    if precision == 6 and mode in (2, 3):
        source.update(layout_sha256=record['layout'], validation_scope='Huffman codewords, lengths and coefficient groups')
    if precision > 6:
        source.update(matrix_reused=mode == 4, group_reused=mode in (2, 3))
        if mode != 1:
            source.update(prior_quantization_bits=6, prior_sha256=prior)
    return source

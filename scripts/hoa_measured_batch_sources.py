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

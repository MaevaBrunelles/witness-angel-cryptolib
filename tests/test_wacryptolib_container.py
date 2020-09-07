import copy
import os
import random
import textwrap
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from _test_mockups import FakeTestContainerStorage
from wacryptolib.container import (
    LOCAL_ESCROW_MARKER,
    encrypt_data_into_container,
    decrypt_data_from_container,
    ContainerStorage,
    extract_metadata_from_container,
    ContainerBase,
    get_encryption_configuration_summary, dump_container_to_filesystem, load_container_from_filesystem,
    SHARED_SECRET_MARKER, get_escrow_id, gather_escrow_dependencies, get_escrow_proxy, request_decryption_authorizations
)
from wacryptolib.escrow import EscrowApi, generate_asymmetric_keypair_for_storage, generate_free_keypair_for_least_provisioned_key_type
from wacryptolib.jsonrpc_client import JsonRpcProxy, status_slugs_response_error_handler
from wacryptolib.key_generation import generate_asymmetric_keypair
from wacryptolib.key_storage import DummyKeyStorage, FilesystemKeyStorage, FilesystemKeyStoragePool, DummyKeyStoragePool
from wacryptolib.utilities import load_from_json_bytes, dump_to_json_bytes, generate_uuid0
from wacryptolib.utilities import dump_to_json_file, load_from_json_file

SIMPLE_CONTAINER_CONF = dict(
    data_encryption_strata=[
        dict(
            data_encryption_algo="AES_CBC",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                )
            ],
            data_signatures=[
                dict(
                    message_prehash_algo="SHA256",
                    signature_algo="DSA_DSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                )
            ],
        )
    ]
)

COMPLEX_CONTAINER_CONF = dict(
    data_encryption_strata=[
        dict(
            data_encryption_algo="AES_EAX",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                )
            ],
            data_signatures=[],
        ),
        dict(
            data_encryption_algo="AES_CBC",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                )
            ],
            data_signatures=[
                dict(
                    message_prehash_algo="SHA3_512",
                    signature_algo="DSA_DSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                )
            ],
        ),
        dict(
            data_encryption_algo="CHACHA20_POLY1305",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                ),
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                ),
            ],
            data_signatures=[
                dict(
                    message_prehash_algo="SHA3_256",
                    signature_algo="RSA_PSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                ),
                dict(
                    message_prehash_algo="SHA512",
                    signature_algo="ECC_DSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                ),
            ],
        ),
    ]
)

SIMPLE_SHAMIR_CONTAINER_CONF = dict(
    data_encryption_strata=[
        dict(
            data_encryption_algo="AES_CBC",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                ),
                dict(
                    key_encryption_algo=SHARED_SECRET_MARKER,
                    key_shared_secret_threshold=3,
                    key_shared_secret_escrows=[
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                    ],
                ),
            ],
            data_signatures=[
                dict(
                    message_prehash_algo="SHA256",
                    signature_algo="DSA_DSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                )
            ],
        )
    ]
)

COMPLEX_SHAMIR_CONTAINER_CONF = dict(
    data_encryption_strata=[
        dict(
            data_encryption_algo="AES_EAX",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                )
            ],
            data_signatures=[],
        ),
        dict(
            data_encryption_algo="AES_CBC",
            key_encryption_strata=[
                dict(
                    key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                )
            ],
            data_signatures=[
                dict(
                    message_prehash_algo="SHA3_512",
                    signature_algo="DSA_DSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                )
            ],
        ),
        dict(
            data_encryption_algo="CHACHA20_POLY1305",
            key_encryption_strata=[
                dict(
                    key_encryption_algo=SHARED_SECRET_MARKER,
                    key_shared_secret_threshold=2,
                    key_shared_secret_escrows=[
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                        dict(
                            share_encryption_algo="RSA_OAEP",
                            share_escrow=LOCAL_ESCROW_MARKER,
                        ),
                    ],
                )
            ],
            data_signatures=[
                dict(
                    message_prehash_algo="SHA3_256",
                    signature_algo="RSA_PSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                ),
                dict(
                    message_prehash_algo="SHA512",
                    signature_algo="ECC_DSS",
                    signature_escrow=LOCAL_ESCROW_MARKER,
                ),
            ],
        ),
    ]
)


@pytest.mark.parametrize(
    "container_conf", [SIMPLE_CONTAINER_CONF, COMPLEX_CONTAINER_CONF]
)
def test_container_encryption_and_decryption(container_conf):
    data = b"abc"  # get_random_bytes(random.randint(1, 1000))

    keychain_uid = random.choice(
        [None, uuid.UUID("450fc293-b702-42d3-ae65-e9cc58e5a62a")]
    )

    key_storage_container = DummyKeyStoragePool()
    metadata = random.choice([None, dict(a=[123])])
    container = encrypt_data_into_container(
        data=data, conf=container_conf, keychain_uid=keychain_uid, metadata=metadata, key_storage_pool=key_storage_container
    )

    escrow_dependencies = gather_escrow_dependencies([container])
    assert isinstance(escrow_dependencies, dict)
    assert escrow_dependencies.get("signature") is not None
    assert escrow_dependencies.get("encryption") is not None

    request_decryption_authorizations(
        escrow_dependencies=escrow_dependencies, request_message="Decryption needed", key_storage_pool=key_storage_container
    )

    assert container["keychain_uid"]
    if keychain_uid:
        assert container["keychain_uid"] == keychain_uid

    result_data = decrypt_data_from_container(container=container, key_storage_pool=key_storage_container)
    # pprint.pprint(result, width=120)
    assert result_data == data

    result_metadata = extract_metadata_from_container(container=container)
    assert result_metadata == metadata

    container["container_format"] = "OAJKB"
    with pytest.raises(ValueError, match="Unknown container format"):
        decrypt_data_from_container(container=container)


@pytest.mark.parametrize(
    "shamir_container_conf",
    [SIMPLE_SHAMIR_CONTAINER_CONF, COMPLEX_SHAMIR_CONTAINER_CONF],
)
def test_shamir_container_encryption_and_decryption(shamir_container_conf):
    data = b"abc"  # get_random_bytes(random.randint(1, 1000))

    keychain_uid = random.choice(
        [None, uuid.UUID("450fc293-b702-42d3-ae65-e9cc58e5a62a")]
    )

    metadata = random.choice([None, dict(a=[123])])

    container = encrypt_data_into_container(
        data=data,
        conf=shamir_container_conf,
        keychain_uid=keychain_uid,
        metadata=metadata,
    )

    escrow_dependencies = gather_escrow_dependencies([container])
    assert isinstance(escrow_dependencies, dict)
    assert escrow_dependencies.get("signature") is not None
    assert escrow_dependencies.get("encryption") is not None

    assert container["keychain_uid"]
    if keychain_uid:
        assert container["keychain_uid"] == keychain_uid

    assert isinstance(container["data_ciphertext"], bytes)

    result_data = decrypt_data_from_container(container=container)

    assert result_data == data

    data_encryption_shamir = {}
    # Delete 1, 2 and too many share(s) from cipherdict key
    for data_encryption in container["data_encryption_strata"]:
        for key_encryption in data_encryption["key_encryption_strata"]:
            if key_encryption["key_encryption_algo"] == SHARED_SECRET_MARKER:
                data_encryption_shamir = data_encryption

    key_ciphertext_shares = load_from_json_bytes(
        data_encryption_shamir["key_ciphertext"]
    )

    # 1 share is deleted
    index = random.randrange(start=1, stop=len(key_ciphertext_shares["shares"]))
    del key_ciphertext_shares["shares"][index]

    data_encryption_shamir["key_ciphertext"] = dump_to_json_bytes(key_ciphertext_shares)

    result_data = decrypt_data_from_container(container=container)
    assert result_data == data

    # Another share is deleted

    index = random.randrange(start=1, stop=len(key_ciphertext_shares["shares"]))
    del key_ciphertext_shares["shares"][index]

    data_encryption_shamir["key_ciphertext"] = dump_to_json_bytes(key_ciphertext_shares)

    result_data = decrypt_data_from_container(container=container)
    assert result_data == data

    # Another share is deleted and now there aren't enough valid ones to decipher data

    index = random.randrange(start=1, stop=len(key_ciphertext_shares["shares"]))
    del key_ciphertext_shares["shares"][index]

    data_encryption_shamir["key_ciphertext"] = dump_to_json_bytes(key_ciphertext_shares)

    with pytest.raises(RuntimeError):
        decrypt_data_from_container(container=container)

    result_metadata = extract_metadata_from_container(container=container)
    assert result_metadata == metadata

    container["container_format"] = "OAJKB"
    with pytest.raises(ValueError, match="Unknown container format"):
        decrypt_data_from_container(container=container)


def test_passphrase_mapping_during_decryption():

    keychain_uid = generate_uuid0()

    keychain_uid_escrow = generate_uuid0()

    local_passphrase = "b^yep&ts"

    key_storage_uid1 = keychain_uid_escrow
    passphrase1 = "tata"

    key_storage_uid2 = generate_uuid0()
    passphrase2 = "2çès"

    key_storage_uid3 = generate_uuid0()
    passphrase3 = "zaizoadsxsnd123"

    all_passphrases = [local_passphrase, passphrase1, passphrase2, passphrase3]

    key_storage_pool = DummyKeyStoragePool()
    key_storage_pool._register_fake_imported_storage_uids(storage_uids=[key_storage_uid1, key_storage_uid2, key_storage_uid3])

    local_key_storage = key_storage_pool.get_local_key_storage()
    generate_asymmetric_keypair_for_storage(
            key_type="RSA_OAEP", key_storage=local_key_storage, keychain_uid=keychain_uid, passphrase=local_passphrase)
    key_storage1 = key_storage_pool.get_imported_key_storage(key_storage_uid1)
    generate_asymmetric_keypair_for_storage(
            key_type="RSA_OAEP", key_storage=key_storage1, keychain_uid=keychain_uid_escrow, passphrase=passphrase1)
    key_storage2 = key_storage_pool.get_imported_key_storage(key_storage_uid2)
    generate_asymmetric_keypair_for_storage(
            key_type="RSA_OAEP", key_storage=key_storage2, keychain_uid=keychain_uid, passphrase=passphrase2)
    key_storage3 = key_storage_pool.get_imported_key_storage(key_storage_uid3)
    generate_asymmetric_keypair_for_storage(
            key_type="RSA_OAEP", key_storage=key_storage3, keychain_uid=keychain_uid, passphrase=passphrase3)

    local_escrow_id = get_escrow_id(LOCAL_ESCROW_MARKER)

    share_escrow1 = dict(escrow_type="key_device", key_device_uid=key_storage_uid1)
    share_escrow1_id = get_escrow_id(share_escrow1)

    share_escrow2 = dict(escrow_type="key_device", key_device_uid=key_storage_uid2)
    share_escrow2_id = get_escrow_id(share_escrow2)

    share_escrow3 = dict(escrow_type="key_device", key_device_uid=key_storage_uid3)
    share_escrow3_id = get_escrow_id(share_escrow3)

    container_conf = dict(
        data_encryption_strata=[
            dict(
                data_encryption_algo="AES_CBC",
                key_encryption_strata=[
                    dict(
                        key_encryption_algo="RSA_OAEP", key_escrow=LOCAL_ESCROW_MARKER
                    ),
                    dict(
                        key_encryption_algo=SHARED_SECRET_MARKER,
                        key_shared_secret_threshold=2,
                        key_shared_secret_escrows=[
                            dict(
                                share_encryption_algo="RSA_OAEP",
                                keychain_uid=keychain_uid_escrow,
                                share_escrow=share_escrow1,
                            ),
                            dict(
                                share_encryption_algo="RSA_OAEP",
                                share_escrow=share_escrow2,
                            ),
                            dict(
                                share_encryption_algo="RSA_OAEP",
                                share_escrow=share_escrow3,
                            ),
                        ],
                    ),
                ],
                data_signatures=[
                    dict(
                        message_prehash_algo="SHA256",
                        signature_algo="DSA_DSS",
                        signature_escrow=LOCAL_ESCROW_MARKER,  # Uses separate keypair, no passphrase here
                    )
                ],
            )
        ]
    )

    data = b"sjzgzj"

    container = encrypt_data_into_container(
        data=data, conf=container_conf, keychain_uid=keychain_uid, key_storage_pool=key_storage_pool, metadata=None
    )

    with pytest.raises(RuntimeError, match="2 valid .* missing for reconstitution"):
        decrypt_data_from_container(container, key_storage_pool=key_storage_pool)

    with pytest.raises(RuntimeError, match="2 valid .* missing for reconstitution"):
        decrypt_data_from_container(container, key_storage_pool=key_storage_pool,
                                    passphrase_mapper={local_escrow_id: all_passphrases})  # Doesn't help share escrows

    with pytest.raises(RuntimeError, match="1 valid .* missing for reconstitution"):
        decrypt_data_from_container(container, key_storage_pool=key_storage_pool,
                                    passphrase_mapper={share_escrow1_id: all_passphrases})  # Unblocks 1 share escrow

    with pytest.raises(RuntimeError, match="1 valid .* missing for reconstitution"):
        decrypt_data_from_container(container, key_storage_pool=key_storage_pool,
                                    passphrase_mapper={share_escrow1_id: all_passphrases, share_escrow2_id: [passphrase3]})  # No changes

    with pytest.raises(ValueError, match="Could not decrypt private key"):
        decrypt_data_from_container(container, key_storage_pool=key_storage_pool,
                                    passphrase_mapper={share_escrow1_id: all_passphrases, share_escrow3_id: [passphrase3]})

    with pytest.raises(ValueError, match="Could not decrypt private key"):
        decrypt_data_from_container(container, key_storage_pool=key_storage_pool,
                                    passphrase_mapper={local_escrow_id: ["qsdqsd"], share_escrow1_id: all_passphrases, share_escrow3_id: [passphrase3]})

    decrypted =  decrypt_data_from_container(container, key_storage_pool=key_storage_pool,
                                        passphrase_mapper={local_escrow_id: [local_passphrase], share_escrow1_id: all_passphrases, share_escrow3_id: [passphrase3]})
    assert decrypted == data
    

def test_get_proxy_for_escrow(tmp_path):
    container_base1 = ContainerBase()
    proxy1 = get_escrow_proxy(LOCAL_ESCROW_MARKER, container_base1._key_storage_pool)
    assert isinstance(proxy1, EscrowApi)  # Local Escrow
    assert isinstance(proxy1._key_storage, DummyKeyStorage)  # Default type

    container_base1_bis = ContainerBase()
    proxy1_bis = get_escrow_proxy(LOCAL_ESCROW_MARKER, container_base1_bis._key_storage_pool)
    assert (
        proxy1_bis._key_storage is proxy1_bis._key_storage
    )  # process-local storage is SINGLETON!

    container_base2 = ContainerBase(
            key_storage_pool=FilesystemKeyStoragePool(str(tmp_path))
    )
    proxy2 = get_escrow_proxy(LOCAL_ESCROW_MARKER, container_base2._key_storage_pool)
    assert isinstance(proxy2, EscrowApi)  # Local Escrow
    assert isinstance(proxy2._key_storage, FilesystemKeyStorage)

    for container_base in (container_base1, container_base2):
        proxy = get_escrow_proxy(
            dict(escrow_type="jsonrpc", url="http://example.com/jsonrpc"), container_base._key_storage_pool
        )
        assert isinstance(
            proxy, JsonRpcProxy
        )  # It should expose identical methods to EscrowApi

        assert proxy._url == "http://example.com/jsonrpc"
        assert proxy._response_error_handler == status_slugs_response_error_handler

        with pytest.raises(ValueError):
            get_escrow_proxy(dict(escrow_type="something-wrong"), container_base._key_storage_pool)

        with pytest.raises(ValueError):
            get_escrow_proxy(dict(urn="athena"), container_base._key_storage_pool)


def test_container_storage_and_executor(tmp_path, caplog):
    # Beware, here we use the REAL ContainerStorage, not FakeTestContainerStorage!
    storage = ContainerStorage(
        encryption_conf=SIMPLE_CONTAINER_CONF, containers_dir=tmp_path
    )
    assert storage._max_containers_count is None
    assert len(storage) == 0
    assert storage.list_container_names() == []

    storage.enqueue_file_for_encryption("animals.dat", b"dogs\ncats\n", metadata=None)
    storage.enqueue_file_for_encryption("empty.txt", b"", metadata=dict(somevalue=True))
    assert len(storage) == 0  # Container threads are just beginning to work!

    storage.wait_for_idle_state()

    assert len(storage) == 2
    assert storage.list_container_names(as_sorted=True) == [
        Path("animals.dat.crypt"),
        Path("empty.txt.crypt"),
    ]

    # Test proper logging of errors occurring in thread pool executor
    assert storage._make_absolute  # Instance method
    storage._make_absolute = None  # Corruption!
    assert "Caught exception" not in caplog.text, caplog.text
    storage.enqueue_file_for_encryption("something.mpg", b"#########", metadata=None)
    storage.wait_for_idle_state()
    assert len(storage) == 2  # Unchanged
    assert "Caught exception" in caplog.text, caplog.text
    del storage._make_absolute
    assert storage._make_absolute  # Back to the method

    abs_entries = storage.list_container_names(as_absolute=True)
    assert len(abs_entries) == 2  # Unchanged
    assert all(entry.is_absolute() for entry in abs_entries)

    animals_content = storage.decrypt_container_from_storage("animals.dat.crypt")
    assert animals_content == b"dogs\ncats\n"

    empty_content = storage.decrypt_container_from_storage("empty.txt.crypt")
    assert empty_content == b""

    assert len(storage) == 2
    os.remove(os.path.join(tmp_path, "animals.dat.crypt"))
    assert storage.list_container_names(as_sorted=True) == [Path("empty.txt.crypt")]
    assert len(storage) == 1

    # Test purge system

    offload_data_ciphertext1 = random.choice((True, False))
    storage = FakeTestContainerStorage(encryption_conf=None, containers_dir=tmp_path,
                                       offload_data_ciphertext=offload_data_ciphertext1)
    assert storage._max_containers_count is None
    for i in range(10):
        storage.enqueue_file_for_encryption("file.dat", b"dogs\ncats\n", metadata=None)
    assert len(storage) < 11  # In progress
    storage.wait_for_idle_state()
    assert len(storage) == 11  # Still the older file remains

    offload_data_ciphertext2 = random.choice((True, False))
    storage = FakeTestContainerStorage(
        encryption_conf=None, containers_dir=tmp_path, max_containers_count=3,
            offload_data_ciphertext=offload_data_ciphertext2
    )
    for i in range(3):
        storage.enqueue_file_for_encryption("xyz.dat", b"abc", metadata=None)
    storage.wait_for_idle_state()
    assert len(storage) == 3  # Purged
    assert storage.list_container_names(as_sorted=True) == [
        Path("xyz.dat.000.crypt"),
        Path("xyz.dat.001.crypt"),
        Path("xyz.dat.002.crypt"),
    ]

    storage.enqueue_file_for_encryption("xyz.dat", b"abc", metadata=None)
    storage.wait_for_idle_state()
    assert len(storage) == 3  # Purged
    assert storage.list_container_names(as_sorted=True) == [
        Path("xyz.dat.001.crypt"),
        Path("xyz.dat.002.crypt"),
        Path("xyz.dat.003.crypt"),
    ]

    offload_data_ciphertext3 = random.choice((True, False))
    storage = FakeTestContainerStorage(
        encryption_conf=None, containers_dir=tmp_path, max_containers_count=4,
            offload_data_ciphertext=offload_data_ciphertext3
    )
    assert len(storage) == 3  # Retrieves existing containers
    storage.enqueue_file_for_encryption("aaa.dat", b"000", metadata=None)
    storage.wait_for_idle_state()
    assert len(storage) == 4  # Unchanged
    storage.enqueue_file_for_encryption("zzz.dat", b"000", metadata=None)
    storage.wait_for_idle_state()
    assert len(storage) == 4  # Purge occurred
    # Entry "aaa.dat.000.crypt" was ejected because it's a sorting by NAMES for now!
    assert storage.list_container_names(as_sorted=True) == [
        Path("xyz.dat.001.crypt"),
        Path("xyz.dat.002.crypt"),
        Path("xyz.dat.003.crypt"),
        Path("zzz.dat.001.crypt"),
    ]


def test_get_encryption_configuration_summary():
    data = b"some data whatever"

    summary = get_encryption_configuration_summary(SIMPLE_CONTAINER_CONF)

    assert summary == textwrap.dedent(
        """\
        Data encryption layer 1: AES_CBC
          Key encryption layers:
            RSA_OAEP (by local device)
          Signatures:
            SHA256/DSA_DSS (by local device)
            """
    )  # Ending by newline!

    container = encrypt_data_into_container(
        data=data, conf=SIMPLE_CONTAINER_CONF, keychain_uid=None, metadata=None
    )
    summary2 = get_encryption_configuration_summary(container)
    assert summary2 == summary  # Identical summary for conf and generated containers!

    # Simulate a conf with remote escrow webservices

    CONF_WITH_ESCROW = copy.deepcopy(COMPLEX_CONTAINER_CONF)
    CONF_WITH_ESCROW["data_encryption_strata"][0]["key_encryption_strata"][0][
        "key_escrow"
    ] = dict(escrow_type="jsonrpc", url="http://www.mydomain.com/json")

    summary = get_encryption_configuration_summary(CONF_WITH_ESCROW)
    assert summary == textwrap.dedent(
        """\
        Data encryption layer 1: AES_EAX
          Key encryption layers:
            RSA_OAEP (by www.mydomain.com)
          Signatures:
        Data encryption layer 2: AES_CBC
          Key encryption layers:
            RSA_OAEP (by local device)
          Signatures:
            SHA3_512/DSA_DSS (by local device)
        Data encryption layer 3: CHACHA20_POLY1305
          Key encryption layers:
            RSA_OAEP (by local device)
            RSA_OAEP (by local device)
          Signatures:
            SHA3_256/RSA_PSS (by local device)
            SHA512/ECC_DSS (by local device)
            """
    )  # Ending by newline!

    _public_key = generate_asymmetric_keypair(key_type="RSA_OAEP")["public_key"]
    with patch.object(
        JsonRpcProxy, "get_public_key", return_value=_public_key, create=True
    ) as mock_method:
        container = encrypt_data_into_container(
            data=data, conf=CONF_WITH_ESCROW, keychain_uid=None, metadata=None
        )
        summary2 = get_encryption_configuration_summary(container)
        assert (
            summary2 == summary
        )  # Identical summary for conf and generated containers!

    # Test unknown escrow structure

    CONF_WITH_BROKEN_ESCROW = copy.deepcopy(SIMPLE_CONTAINER_CONF)
    CONF_WITH_BROKEN_ESCROW["data_encryption_strata"][0]["key_encryption_strata"][0][
        "key_escrow"
    ] = dict(abc=33)

    with pytest.raises(ValueError, match="Unrecognized key escrow"):
        get_encryption_configuration_summary(CONF_WITH_BROKEN_ESCROW)


@pytest.mark.parametrize(
    "container_conf", [SIMPLE_CONTAINER_CONF, COMPLEX_CONTAINER_CONF]
)
def test_filesystem_container_loading_and_dumping(tmp_path, container_conf):

    data = b"jhf"

    keychain_uid = random.choice(
        [None, uuid.UUID("450fc293-b702-42d3-ae65-e9cc58e5a62a")]
    )

    metadata = random.choice([None, dict(a=[123])])

    container = encrypt_data_into_container(
        data=data, conf=container_conf, keychain_uid=keychain_uid, metadata=metadata
    )
    container_ciphertext_before_dump = container["data_ciphertext"]

    container_without_ciphertext = copy.deepcopy(container)
    del container_without_ciphertext["data_ciphertext"]

    # CASE 1 - MONOLITHIC JSON FILE

    container_filepath = tmp_path / "mycontainer_monolithic.crypt"
    dump_container_to_filesystem(container_filepath, container=container, offload_data_ciphertext=False)
    container_reloaded = load_from_json_file(container_filepath)
    assert container_reloaded["data_ciphertext"] == container_ciphertext_before_dump  # NO OFFLOADING
    assert load_container_from_filesystem(container_filepath) == container  # UNCHANGED from original

    container_truncated = load_container_from_filesystem(container_filepath, include_data_ciphertext=False)
    assert "data_ciphertext" not in container_truncated
    assert container_truncated == container_without_ciphertext

    assert container["data_ciphertext"] == container_ciphertext_before_dump # Original dict unchanged

    # CASE 2 - OFFLOADED CIPHERTEXT FILE

    dump_container_to_filesystem(container_filepath, container=container)  # OVERWRITE, with offloading by default
    container_reloaded = load_from_json_file(container_filepath)
    assert container_reloaded["data_ciphertext"] == "[OFFLOADED]"

    container_offloaded_filepathstr = str(container_filepath) + ".data"
    offloaded_data_reloaded = Path(container_offloaded_filepathstr).read_bytes()
    assert offloaded_data_reloaded == container_ciphertext_before_dump  # WELL OFFLOADED as DIRECT BYTES
    assert load_container_from_filesystem(container_filepath) == container  # UNCHANGED from original

    container_truncated = load_container_from_filesystem(container_filepath, include_data_ciphertext=False)
    assert "data_ciphertext" not in container_truncated
    assert container_truncated == container_without_ciphertext

    assert container["data_ciphertext"] == container_ciphertext_before_dump # Original dict unchanged

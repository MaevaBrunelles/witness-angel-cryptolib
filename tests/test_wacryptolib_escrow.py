import os
import random
import uuid

import pytest
from Crypto.Random import get_random_bytes

from wacryptolib.encryption import _encrypt_via_rsa_oaep
from wacryptolib.escrow import EscrowApi, DummyKeyStorage, KeyStorageBase, FilesystemKeyStorage
from wacryptolib.key_generation import load_asymmetric_key_from_pem_bytestring
from wacryptolib.signature import verify_message_signature
from wacryptolib.utilities import generate_uuid0


def test_escrow_api_workflow():

    key_storage = DummyKeyStorage()
    escrow_api = EscrowApi(key_storage=key_storage)

    keychain_uid = generate_uuid0()
    secret = get_random_bytes(101)

    public_key_pem = escrow_api.get_public_key(
        keychain_uid=keychain_uid, key_type="RSA"
    )
    public_key = load_asymmetric_key_from_pem_bytestring(
        key_pem=public_key_pem, key_type="RSA"
    )

    signature = escrow_api.get_message_signature(
        keychain_uid=keychain_uid, message=secret, key_type="RSA", signature_algo="PSS"
    )
    verify_message_signature(
        message=secret, signature=signature, key=public_key, signature_algo="PSS"
    )

    signature["digest"] += b"xyz"
    with pytest.raises(ValueError, match="Incorrect signature"):
        verify_message_signature(
            message=secret, signature=signature, key=public_key, signature_algo="PSS"
        )

    cipherdict = _encrypt_via_rsa_oaep(plaintext=secret, key=public_key)

    decrypted = escrow_api.decrypt_with_private_key(
        keychain_uid=keychain_uid,
        key_type="RSA",
        encryption_algo="RSA_OAEP",
        cipherdict=cipherdict,
    )

    cipherdict["digest_list"].append(b"aaabbbccc")
    with pytest.raises(ValueError, match="Ciphertext with incorrect length"):
        escrow_api.decrypt_with_private_key(
            keychain_uid=keychain_uid,
            key_type="RSA",
            encryption_algo="RSA_OAEP",
            cipherdict=cipherdict,
        )

    assert decrypted == secret


def test_key_storages(tmp_path):

    dummy_key_storage = DummyKeyStorage()
    filesystem_key_storage = FilesystemKeyStorage(keys_dir=str(tmp_path))

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        KeyStorageBase()

    # Sanity checks on dummy key storage used

    keychain_uid = generate_uuid0()
    keychain_uid_other = generate_uuid0()

    for key_storage in (dummy_key_storage, filesystem_key_storage):

        key_storage.set_keys(
            keychain_uid=keychain_uid, key_type="abxz", public_key=b"public_data", private_key=b"private_data"
        )
        with pytest.raises(RuntimeError):
            key_storage.set_keys(
                keychain_uid=keychain_uid,
                key_type="abxz",
                public_key=b"public_data",
                private_key=b"private_data",
            )
        with pytest.raises(RuntimeError):
            key_storage.set_keys(
                keychain_uid=keychain_uid,
                key_type="abxz",
                public_key=b"public_data2",
                private_key=b"private_data2",
            )

        assert key_storage.get_public_key(keychain_uid=keychain_uid, key_type="abxz") == b"public_data"
        assert key_storage.get_private_key(keychain_uid=keychain_uid, key_type="abxz") == b"private_data"

        assert key_storage.get_public_key(keychain_uid=keychain_uid, key_type="abxz_") == None
        assert key_storage.get_private_key(keychain_uid=keychain_uid, key_type="abxz_") == None

        assert key_storage.get_public_key(keychain_uid=keychain_uid_other, key_type="abxz") == None
        assert key_storage.get_private_key(keychain_uid=keychain_uid_other, key_type="abxz") == None


    is_public = random.choice([True, False])
    basename = filesystem_key_storage._get_filename(keychain_uid, key_type="abxz", is_public=is_public)
    with open(os.path.join(str(tmp_path), basename), "rb") as f:
        key_data = f.read()
        assert key_data == (b"public_data" if is_public else b"private_data")  # IMPORTANT no exchange of keys in files!

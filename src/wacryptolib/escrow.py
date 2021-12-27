import logging
import time
import uuid
from typing import Optional, Union, AnyStr, Sequence
from uuid import UUID

from wacryptolib.encryption import _decrypt_via_rsa_oaep
from wacryptolib.exceptions import KeyDoesNotExist, AuthorizationError, DecryptionError, KeyLoadingError
from wacryptolib.key_generation import (
    generate_keypair,
    load_asymmetric_key_from_pem_bytestring,
    SUPPORTED_ASYMMETRIC_KEY_ALGOS,
)
from wacryptolib.key_storage import KeyStorageBase as KeyStorageBase
from wacryptolib.signature import sign_message
from wacryptolib.utilities import PeriodicTaskHandler, generate_uuid0

logger = logging.getLogger(__name__)


MAX_PAYLOAD_LENGTH_FOR_SIGNATURE = 128  # Max 2*SHA512 length


def generate_keypair_for_storage(  # FIXME rename and add to docs
    key_algo: str, *, key_storage, keychain_uid: Optional[UUID] = None, passphrase: Optional[AnyStr] = None
) -> dict:
    """
    Shortcut to generate an asymmetric keypair and store it into a key storage.

    `keychain_uid` is auto-generated if not provided.

    Returns the generated keypair dict.
    """
    from wacryptolib.key_generation import generate_keypair

    keychain_uid = keychain_uid or generate_uuid0()
    keypair = generate_keypair(key_algo=key_algo, serialize=True, passphrase=passphrase)
    key_storage.set_keys(
        keychain_uid=keychain_uid,
        key_algo=key_algo,
        public_key=keypair["public_key"],
        private_key=keypair["private_key"],
    )
    return keypair


class EscrowApi:
    """
    This is the API meant to be exposed by escrow webservices, to allow end users to create safely encrypted cryptainers.

    Subclasses must add their own permission checking, especially so that no decryption with private keys can occur
    outside the scope of a well defined legal procedure.
    """

    def __init__(self, key_storage: KeyStorageBase):
        self._key_storage = key_storage

    def _ensure_keypair_exists(self, keychain_uid: uuid.UUID, key_algo: str):
        """Create a keypair if it doesn't exist."""

        try:
            self._key_storage.get_public_key(keychain_uid=keychain_uid, key_algo=key_algo)
        except KeyDoesNotExist:
            pass
        else:
            return  # Ok the key is available!

        try:
            self._key_storage.attach_free_keypair_to_uuid(keychain_uid=keychain_uid, key_algo=key_algo)
        except KeyDoesNotExist:
            generate_keypair_for_storage(
                key_algo=key_algo, key_storage=self._key_storage, keychain_uid=keychain_uid, passphrase=None
            )

    def fetch_public_key(self, *, keychain_uid: uuid.UUID, key_algo: str, must_exist: bool = False) -> bytes:
        """
        Return a public key in PEM format bytestring, that caller shall use to encrypt its own symmetric keys,
        or to check a signature.

        If `must_exist` is True, key is not autogenerated, and a KeyDoesNotExist might be raised.
        """
        if not must_exist:
            self._ensure_keypair_exists(keychain_uid=keychain_uid, key_algo=key_algo)
        return self._key_storage.get_public_key(
            keychain_uid=keychain_uid, key_algo=key_algo
        )  # Let the exception flow if any

    def get_message_signature(
        self, *, keychain_uid: uuid.UUID, message: bytes, signature_algo: str  # FIXME name this "key_algo" too?
    ) -> dict:
        """
        Return a signature structure corresponding to the provided key and signature types.
        """

        if len(message) > MAX_PAYLOAD_LENGTH_FOR_SIGNATURE:  # SECURITY
            raise ValueError("Message too big for signing, only a hash should be sent")

        self._ensure_keypair_exists(keychain_uid=keychain_uid, key_algo=signature_algo)

        private_key_pem = self._key_storage.get_private_key(keychain_uid=keychain_uid, key_algo=signature_algo)

        private_key = load_asymmetric_key_from_pem_bytestring(key_pem=private_key_pem, key_algo=signature_algo)

        signature = sign_message(message=message, signature_algo=signature_algo, key=private_key)
        return signature

    def _check_keypair_authorization(self, *, keychain_uid: uuid.UUID, key_algo: str):
        """raises a proper exception if authorization is not given yet to decrypt with this keypair."""
        return  # In this base implementation we always allow decryption!

    def _decrypt_private_key_pem_with_passphrases(
        self, *, private_key_pem: bytes, key_algo: str, passphrases: Optional[list]
    ):
        """
        Attempt decryption of key with and without provided passphrases, and raise if all fail.
        """
        for passphrase in [None] + passphrases:
            try:
                key_obj = load_asymmetric_key_from_pem_bytestring(
                    key_pem=private_key_pem, key_algo=key_algo, passphrase=passphrase
                )
                return key_obj
            except KeyLoadingError:
                pass
        raise DecryptionError(
            "Could not decrypt private key of type %s (passphrases provided: %d)" % (key_algo, len(passphrases))
        )

    def request_decryption_authorization(
        self, keypair_identifiers: Sequence, request_message: str, passphrases: Optional[Sequence] = None
    ) -> dict:
        """
        Send a list of keypairs for which decryption access is requested, with the reason why.

        If request is immediately denied, an exception is raised, else the status of the authorization process
        (process which might involve several steps, including live encounters) is returned.

        :param keypair_identifiers: list of dicts with (keychain_uid, key_algo) indices to authorize
        :param request_message: user text explaining the reasons for the decryption (and the legal procedures involved)
        :param passphrases: optional list of passphrases to be tried on private keys
        :return: a dict with at least a string field "response_message" detailing the status of the request.
        """

        passphrases = passphrases or []
        assert isinstance(passphrases, (tuple, list)), repr(passphrases)

        if not keypair_identifiers:
            raise ValueError("Keypair identifiers must not be empty, when requesting decryption authorization")

        missing_private_key = []
        authorization_missing = []
        missing_passphrase = []
        accepted = []

        for keypair_identifier in keypair_identifiers:

            keychain_uid = keypair_identifier["keychain_uid"]
            key_algo = keypair_identifier["key_algo"]

            try:
                self._check_keypair_authorization(keychain_uid=keychain_uid, key_algo=key_algo)
            except AuthorizationError:
                authorization_missing.append(keypair_identifier)
                continue
            else:
                pass  # It's OK, at least we are authorized now

            try:
                private_key_pem = self._key_storage.get_private_key(keychain_uid=keychain_uid, key_algo=key_algo)
            except KeyDoesNotExist:
                missing_private_key.append(keypair_identifier)
                continue

            try:
                res = self._decrypt_private_key_pem_with_passphrases(
                    private_key_pem=private_key_pem, key_algo=key_algo, passphrases=passphrases
                )
                assert res, repr(res)
            except DecryptionError:
                missing_passphrase.append(keypair_identifier)
                continue

            accepted.append(keypair_identifier)  # Check is OVER for this keypair!

        keypair_statuses = dict(
            missing_private_key=missing_private_key,
            authorization_missing=authorization_missing,
            missing_passphrase=missing_passphrase,
            accepted=accepted,
        )

        has_errors = len(accepted) < len(keypair_identifiers)
        assert sum(len(x) for x in keypair_statuses.values()) == len(keypair_identifiers), locals()

        return dict(
            response_message="Decryption request denied" if has_errors else "Decryption request accepted",
            has_errors=has_errors,
            keypair_statuses=keypair_statuses,
        )  # TODO localize string field!

    def decrypt_with_private_key(
        self, *, keychain_uid: uuid.UUID, encryption_algo: str, cipherdict: dict, passphrases: Optional[list] = None
    ) -> bytes:
        """
        Return the message (probably a symmetric key) decrypted with the corresponding key,
        as bytestring.

        Raises if key existence, authorization or passphrase errors occur.
        """
        assert encryption_algo.upper() == "RSA_OAEP"  # Only supported asymmetric cipher for now

        passphrases = passphrases or []
        assert isinstance(passphrases, (tuple, list)), repr(passphrases)

        private_key_pem = self._key_storage.get_private_key(keychain_uid=keychain_uid, key_algo=encryption_algo)

        private_key = self._decrypt_private_key_pem_with_passphrases(
            private_key_pem=private_key_pem, key_algo=encryption_algo, passphrases=passphrases
        )

        secret = _decrypt_via_rsa_oaep(cipherdict=cipherdict, key_dict=dict(key=private_key))
        return secret


class ReadonlyEscrowApi(EscrowApi):
    """
    Alternative Escrow API which relies on a fixed set of keys (e.g. imported from a key-device).

    This version never generates keys by itself, whatever the values of method parameters like `must_exist`.
    """

    def _ensure_keypair_exists(self, keychain_uid: uuid.UUID, key_algo: str):
        try:
            self._key_storage.get_public_key(keychain_uid=keychain_uid, key_algo=key_algo)
        except KeyDoesNotExist:
            # Just tweak the error message here
            raise KeyDoesNotExist("Keypair %s/%s not found in escrow api" % (keychain_uid, key_algo))


def generate_free_keypair_for_least_provisioned_key_algo(
    key_storage: KeyStorageBase,
    max_free_keys_per_algo: int,
    key_generation_func=generate_keypair,
    key_algos=SUPPORTED_ASYMMETRIC_KEY_ALGOS,
):
    """
    Generate a single free keypair for the key type which is the least available in key storage, and
    add it to storage. If the "free keys" pools of the storage are full, do nothing.

    :param key_storage: the key storage to use
    :param max_free_keys_per_algo: how many free keys should exist per key type
    :param key_generation_func: callable to use for keypair generation
    :param key_algos: the different key types (strings) to consider
    :return: True iff a key was generated (i.e. the free keys pool was not full)
    """
    assert key_algos, key_algos
    free_keys_counts = [(key_storage.get_free_keypairs_count(key_algo), key_algo) for key_algo in key_algos]
    logger.debug("Stats of free keys: %s", str(free_keys_counts))

    (count, key_algo) = min(free_keys_counts)

    if count >= max_free_keys_per_algo:
        return False

    keypair = key_generation_func(key_algo=key_algo, serialize=True)
    key_storage.add_free_keypair(
        key_algo=key_algo, public_key=keypair["public_key"], private_key=keypair["private_key"]
    )
    logger.debug("New free key of type %s pregenerated" % key_algo)
    return True


def get_free_keys_generator_worker(
    key_storage: KeyStorageBase, max_free_keys_per_algo: int, sleep_on_overflow_s: float, **extra_generation_kwargs
) -> PeriodicTaskHandler:
    """
    Return a periodic task handler which will gradually fill the pools of free keys of the key storage,
    and wait longer when these pools are full.
    
    :param key_storage: the key storage to use 
    :param max_free_keys_per_algo: how many free keys should exist per key type
    :param sleep_on_overflow_s: time to wait when free keys pools are full
    :param extra_generation_kwargs: extra arguments to transmit to `generate_free_keypair_for_least_provisioned_key_algo()`
    :return: periodic task handler
    """

    def free_keypair_generator_task():
        has_generated = generate_free_keypair_for_least_provisioned_key_algo(
            key_storage=key_storage, max_free_keys_per_algo=max_free_keys_per_algo, **extra_generation_kwargs
        )
        # TODO - improve this with refactored multitimer, later
        if not has_generated:
            time.sleep(sleep_on_overflow_s)
        return has_generated

    periodic_task_handler = PeriodicTaskHandler(interval_s=0.001, task_func=free_keypair_generator_task)
    return periodic_task_handler

from cryptography.fernet import Fernet

from h1monitor.config import Settings
from h1monitor.store import Store
from h1monitor.main import seed_credentials_if_present


def test_seed_credentials(tmp_path):
    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    s = Settings("bot", None, ":memory:", Fernet.generate_key(), "seedid", "seedtok", None)
    seed_credentials_if_present(st, s)
    assert st.get_h1_credentials() == ("seedid", "seedtok")


def test_seed_does_not_overwrite(tmp_path):
    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    st.set_h1_credentials("existing", "creds")
    s = Settings("bot", None, ":memory:", Fernet.generate_key(), "seedid", "seedtok", None)
    seed_credentials_if_present(st, s)
    assert st.get_h1_credentials() == ("existing", "creds")

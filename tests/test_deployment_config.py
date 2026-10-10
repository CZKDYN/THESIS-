from dormenergy import normalize_database_url


def test_managed_postgresql_url_uses_installed_psycopg_driver():
    for prefix in ('postgres://', 'postgresql://'):
        url = prefix + 'role:placeholder@db.example.com/app?sslmode=verify-full'
        assert normalize_database_url(url) == 'postgresql+psycopg://role:placeholder@db.example.com/app?sslmode=verify-full'
    qualified = 'postgresql+psycopg://role@/app?host=/tmp/socket'
    assert normalize_database_url(qualified) == qualified
    assert normalize_database_url('sqlite:///test.sqlite') == 'sqlite:///test.sqlite'

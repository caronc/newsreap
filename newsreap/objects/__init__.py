

from sqlalchemy import BigInteger
from sqlalchemy import Integer
from sqlalchemy import String

# A number (or id) that can go past 2 billion: BIGINT on database servers;
# SQLite's INTEGER already holds that much (and only an INTEGER primary key
# numbers itself there)
BigNumber = BigInteger().with_variant(Integer(), 'sqlite')


def Key(length):
    """
    Text that is matched exactly (ie: a Message-ID, a key or a group name):
    MySQL otherwise sees 'A.rar' and 'a.rar' (or 'é' and 'e') as the same
    """
    return String(length).with_variant(
        String(length, collation='utf8mb4_bin'), 'mysql', 'mariadb')

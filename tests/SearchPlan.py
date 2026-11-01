# Helps tests check that searches use their search index
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

from sqlalchemy import event


def search_plan(session, search):
    """
    Returns the query plan (as text, one line per step) of every query
    search() runs, as the database would carry it out

    PostgreSQL is told not to read whole tables when it has an index to
    use; a test database is too small for it to bother otherwise.
    """
    engine = session.get_bind()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        search()

    finally:
        event.remove(engine, 'before_cursor_execute', capture)

    # Let go of what the search read (MySQL won't analyze a table another
    # transaction is still using)
    session.commit()

    dialect = engine.dialect.name
    explain = 'EXPLAIN QUERY PLAN ' if dialect == 'sqlite' else 'EXPLAIN '
    lines = []
    with engine.connect() as conn:
        if dialect == 'postgresql':
            conn.exec_driver_sql('SET enable_seqscan = off')

        for statement, parameters in statements:
            if not statement.lstrip().upper().startswith('SELECT'):
                continue

            for row in conn.exec_driver_sql(
                    explain + statement, parameters or ()):
                lines.append(' | '.join(str(value) for value in row))

    return '\n'.join(lines)

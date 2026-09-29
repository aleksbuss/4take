def run_migration(pool, statements):
    conn = pool.acquire()
    cur = conn.cursor()
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()
    pool.release(conn)
    return len(statements)

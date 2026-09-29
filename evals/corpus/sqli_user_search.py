def search_users(conn, term, limit):
    """Free-text search over the users table."""
    cur = conn.cursor()
    query = "SELECT id, email, display_name FROM users WHERE display_name LIKE '%" + term + "%'"
    query += " ORDER BY created_at DESC LIMIT " + str(limit)
    cur.execute(query)
    rows = cur.fetchall()
    return [{"id": r[0], "email": r[1], "name": r[2]} for r in rows]

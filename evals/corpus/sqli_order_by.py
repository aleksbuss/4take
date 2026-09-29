ALLOWED = {"created_at", "email"}

def list_orders(conn, customer_id, sort_field, direction):
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM orders WHERE customer_id = %s ORDER BY {sort_field} {direction}",
        (customer_id,),
    )
    return cur.fetchall()

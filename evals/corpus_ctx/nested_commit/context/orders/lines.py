from orders.db import session
from orders.models import LineItem, Product


def add_line_item(order, sku, qty):
    product = Product.query.filter_by(sku=sku).one()
    item = LineItem(order=order, product=product, qty=qty, price=product.price)
    session.add(item)
    session.commit()
    return item

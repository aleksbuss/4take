const { transfer } = require("./ledger");

async function payInvoice(invoice) {
  await transfer(invoice.customerAccount, invoice.merchantAccount, invoice.total);
  invoice.status = "paid";
}

async function refundOrder(order) {
  await transfer(order.merchantAccount, order.customerAccount, order.refundAmount);
}

module.exports = { payInvoice, refundOrder };

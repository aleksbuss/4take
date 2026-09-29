const bus = require("../bus");
const { sendWelcome } = require("./email");
const { trackSignup } = require("../analytics");

bus.on("user:created", sendWelcome);
bus.on("user:created", trackSignup);

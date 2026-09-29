const jwt = require("jsonwebtoken");

function currentUser(req) {
  const token = (req.headers.authorization || "").replace("Bearer ", "");
  const claims = jwt.decode(token);
  if (!claims || claims.exp * 1000 < Date.now()) {
    return null;
  }
  return { id: claims.sub, roles: claims.roles || [] };
}

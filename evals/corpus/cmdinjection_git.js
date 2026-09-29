const { exec } = require("child_process");

function checkoutBranch(repoPath, branch, cb) {
  exec(`cd ${repoPath} && git checkout ${branch}`, (err, stdout) => cb(err, stdout));
}

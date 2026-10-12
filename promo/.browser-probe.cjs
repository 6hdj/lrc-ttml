const {ensureBrowser} = require("@remotion/renderer");
ensureBrowser({logLevel: "info"}).then(r => console.log("BROWSER OK:", JSON.stringify(r)))
  .catch(e => { console.log("BROWSER FAIL:", e.message); process.exit(1); });

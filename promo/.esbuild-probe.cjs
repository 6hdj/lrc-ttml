const esbuild = require("esbuild");
esbuild.build({
  entryPoints: ["src/index.ts"],
  bundle: false,
  write: false,
  outdir: "out",
}).then(r => console.log("ESBUILD OK, files:", r.outputFiles.length))
  .catch(e => { console.log("ESBUILD FAIL:", e.message.split("\n").slice(0,6).join(" | ")); process.exit(1); });

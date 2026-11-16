/* eslint-disable @typescript-eslint/no-require-imports -- Node --require preload uses CommonJS. */
// Preload for isolated builds/tests; IPC pipes used by build workers remain available.
const net = require("node:net");
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const options = args[0];
  if (typeof options === "object" && (options.path || options[0]?.path)) {
    return connect.apply(this, args);
  }
  if (typeof options === "string" && !/^\d+$/.test(options)) {
    return connect.apply(this, args);
  }
  throw new Error("TCP network access is forbidden in offline builds/tests");
};
globalThis.fetch = async () => { throw new Error("Offline build: API unavailable"); };

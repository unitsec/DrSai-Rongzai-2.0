const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("../webui/frontend/node_modules/typescript");

const source = fs.readFileSync(
  path.join(__dirname, "../webui/frontend/src/pages/chat/hooks/useChatWebSocket.ts"),
  "utf8"
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function setup() {
  let current = { id: "run-1", status: "pausing", messages: [] };
  let closed = false;
  const errors = [];
  const socket = { close: () => { closed = true; } };
  const react = {
    useState: () => [socket, () => {}],
    useRef: (value) => ({ current: value }),
    useCallback: (callback) => callback,
    useEffect: () => {},
  };
  const context = {
    exports: {},
    console,
    clearTimeout: () => {},
    require: (name) => {
      if (name === "react") return react;
      if (name === "antd") return { message: { error: (value) => errors.push(value) } };
      if (name === "../../../utils/chatHelpers") {
        return { createMessage: (config) => ({ config }) };
      }
      throw new Error(`Unexpected import: ${name}`);
    },
  };
  vm.runInNewContext(compiled, context);
  const hook = context.exports.useChatWebSocket({
    session: { id: 1 },
    getSessionSocket: () => socket,
    setCurrentRun: (update) => { current = update(current); },
  });
  hook.setupWebSocket("run-1");
  return {
    hook,
    socket,
    errors,
    get current() { return current; },
    get closed() { return closed; },
    send: (message) => socket.onmessage({ data: JSON.stringify(message) }),
  };
}

test("pause failure restores the server status and keeps receiving messages", () => {
  const state = setup();
  state.hook.inputTimeoutRef.current = 123;
  state.send({
    type: "error",
    operation: "pause",
    status: "active",
    error: "Pause failed: worker unavailable",
  });
  assert.equal(state.current.status, "active");
  assert.equal(state.closed, false);
  assert.equal(state.hook.activeSocketRef.current, state.socket);
  assert.equal(state.hook.inputTimeoutRef.current, 123);
  assert.deepEqual(state.errors, ["Pause failed: worker unavailable"]);
  state.send({ type: "message", data: { source: "assistant", content: "still running" } });
  assert.equal(state.current.messages.at(-1).config.content, "still running");
  state.send({ type: "system", status: "paused" });
  assert.equal(state.current.status, "paused");
});

test("unrelated errors retain the existing socket-close behavior", () => {
  const state = setup();
  state.send({ type: "error", error: "stream failed" });
  assert.equal(state.closed, true);
  assert.equal(state.hook.activeSocketRef.current, null);
});

// Sleep On It: one seeded Minecraft "day" for a crew of villager bots.
//
// Starts a flying-squid server on our own seeded world, connects one Mineflayer bot per villager,
// and speaks JSON lines on stdin/stdout so the Python orchestrator (and Nemotron) can drive it:
//   {"id":1,"op":"init"}                                   -> {"id":1,"ok":true,"result":{...}}
//   {"id":2,"op":"act","bot":"ash","name":"collect","args":{"block":"oak_log","count":3}}
//   {"id":3,"op":"status","bot":"ash"}    {"op":"score"}    {"op":"test_skill","source":"..."}
// All logging goes to stderr; stdout is protocol only.
'use strict'
const realLog = console.log.bind(console)
console.log = (...a) => console.error(...a)

const path = require('path')
const fs = require('fs')
const vm = require('vm')
const readline = require('readline')
const Vec3 = require('vec3').Vec3

const argv = require('./args')(process.argv.slice(2))
const SEED = parseInt(argv.seed ?? '0')
const PORT = parseInt(argv.port ?? String(26000 + (SEED % 2000)))
const BOTS = (argv.bots ?? 'ash').split(',').filter(Boolean)
const DAY_SECONDS = parseFloat(argv.day ?? '240')
const SKILLS_DIR = argv.skills
const VERSION = '1.16.5'
const TEST_MODE = !!argv.test

const recipes = require('./recipes')
const shelter = require('./shelter')

let mcServer, mineflayer, pathfinder, Movements, goals
const bots = {}
const state = {
  started: false,
  dayStart: 0,
  placed: new Map(), // "x,y,z" -> {name, by}
  gained: {}, // item -> count gained by gathering (not crafting)
  crafted: {}, // item -> count crafted
  events: [], // [{t, bot, text}]
  board: [] // team message board (say)
}
let serv
let skills = {}

function send (obj) { process.stdout.write(JSON.stringify(obj) + '\n') }
function secondsLeft () { return state.started ? Math.max(0, DAY_SECONDS - (Date.now() - state.dayStart) / 1000) : DAY_SECONDS }
function isNight () { return state.started && secondsLeft() <= 0 }
function event (bot, text) { state.events.push({ t: Math.round((Date.now() - state.dayStart) / 1000), bot, text }) }
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms))
const key = (p) => `${p.x},${p.y},${p.z}`
const fmt = (p) => p ? `(${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)})` : '?'

function startServer () {
  mcServer = require('flying-squid')
  return new Promise((resolve) => {
    serv = mcServer.createMCServer({
      motd: 'Sleep On It', port: PORT, 'max-players': 10, 'online-mode': false, logging: false, gameMode: 0,
      difficulty: 0, worldFolder: undefined, generation: { name: path.resolve(__dirname, 'worldgen.js'), options: { seed: SEED } },
      kickTimeout: 30000, plugins: {}, modpe: false, 'view-distance': 4, 'player-list-text': { header: '', footer: '' },
      'everybody-op': true, 'max-entities': 100, version: VERSION
    })
    serv.on('listening', resolve)
  })
}

function serverPlayer (name) { return serv.players.find(p => p.username === name) }

// ---------------------------------------------------------------- server-side inventory (crafting is our game rule)
function invCount (name, item) {
  const p = serverPlayer(name)
  return p.inventory.items().filter(i => i.name === item).reduce((a, i) => a + i.count, 0)
}
function invTake (name, item, n) {
  const p = serverPlayer(name)
  for (const it of p.inventory.items()) {
    if (n <= 0) break
    if (it.name !== item) continue
    const k = Math.min(n, it.count)
    n -= k
    it.count -= k
    p.inventory.updateSlot(it.slot, it.count > 0 ? it : null)
  }
}
function invGive (name, item, n) {
  const p = serverPlayer(name)
  const Item = require('prismarine-item')(serv.registry)
  const data = serv.registry.itemsByName[item]
  for (const it of p.inventory.items()) {
    if (it.name === item && it.count < it.stackSize) {
      const k = Math.min(n, it.stackSize - it.count)
      it.count += k
      n -= k
      p.inventory.updateSlot(it.slot, it)
      if (n <= 0) return
    }
  }
  while (n > 0) {
    const slot = p.inventory.firstEmptyInventorySlot()
    if (slot === null || slot === undefined) return
    const k = Math.min(n, data.stackSize)
    p.inventory.updateSlot(slot, new Item(data.id, k))
    n -= k
  }
}

// ---------------------------------------------------------------- bots
async function spawnBot (name) {
  const bot = mineflayer.createBot({ host: 'localhost', port: PORT, username: name, version: VERSION })
  bot.loadPlugin(pathfinder)
  await new Promise((resolve, reject) => {
    bot.once('spawn', resolve)
    bot.once('error', reject)
    bot.once('kicked', (r) => reject(new Error('kicked ' + r)))
  })
  bot.on('error', (e) => console.error(name, 'error', e.message))
  await bot.waitForChunksToLoad?.().catch(() => {})
  const mv = new Movements(bot)
  mv.canDig = true
  mv.allow1by1towers = true
  mv.scafoldingBlocks = []
  bot.pathfinder.setMovements(mv)
  bot.pathfinder.thinkTimeout = 4000
  bots[name] = { bot, busy: false, last: '' }
  return bot
}

function invSummary (bot) {
  const counts = {}
  for (const it of bot.inventory.items()) counts[it.name] = (counts[it.name] || 0) + it.count
  return counts
}

function nearest (bot, blockName, count = 5, maxDistance = 48) {
  const ids = []
  const b = bot.registry.blocksByName[blockName]
  if (!b) return []
  ids.push(b.id)
  const pts = bot.findBlocks({ matching: ids, maxDistance, count: Math.max(count * 4, 20) })
  const me = bot.entity.position
  return pts
    .filter(p => blockName !== 'stone' || exposed(bot, p))
    .map(p => ({ x: p.x, y: p.y, z: p.z, dist: Math.round(p.distanceTo(me)) }))
    .sort((a, b) => a.dist - b.dist)
    .slice(0, count)
}

function exposed (bot, p) {
  for (const d of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]) {
    const b = bot.blockAt(p.offset(...d))
    if (b && b.name === 'air') return true
  }
  return false
}

// ---------------------------------------------------------------- primitives (what Nemotron calls)
function makeApi (name, opts = {}) {
  const bot = bots[name].bot
  const ctl = { cancelled: false, deadline: opts.deadline || Infinity }
  const guard = () => {
    if (ctl.cancelled) throw new Error('skill timed out')
    if (!TEST_MODE && isNight()) throw new Error('night has fallen; the day is over')
  }
  const withTimeout = (p, ms, what) => Promise.race([p, sleep(ms).then(() => { throw new Error(`${what} timed out after ${ms / 1000}s`) })])

  async function goto (x, y, z, range = 1) {
    guard()
    const g = new goals.GoalNear(Math.floor(x), Math.floor(y), Math.floor(z), range)
    try {
      await withTimeout(bot.pathfinder.goto(g), 25000, 'goto')
    } catch (e) {
      bot.pathfinder.stop()
      throw new Error(`could not reach ${fmt({ x, y, z })}: ${e.message}`)
    }
    return `arrived near ${fmt({ x, y, z })}, now at ${fmt(bot.entity.position)}`
  }

  async function equipBest (block) {
    const tool = bot.pathfinder.bestHarvestTool(block)
    if (tool) await bot.equip(tool, 'hand').catch(() => {})
  }

  async function dig (x, y, z) {
    guard()
    const p = new Vec3(Math.floor(x), Math.floor(y), Math.floor(z))
    const block = bot.blockAt(p)
    if (!block || block.name === 'air' || block.name === 'water') throw new Error(`nothing to dig at ${fmt(p)}`)
    if (bot.entity.position.distanceTo(p.offset(0.5, 0.5, 0.5)) > 4.2) {
      try {
        await withTimeout(bot.pathfinder.goto(new goals.GoalLookAtBlock(p, bot.world, { reach: 4 })), 25000, 'walk to block')
      } catch (e) { bot.pathfinder.stop(); throw new Error(`could not reach block ${block.name} at ${fmt(p)}: ${e.message}`) }
    }
    guard()
    const before = invSummary(bot)
    await equipBest(block)
    await withTimeout(bot.dig(block, true), 20000, 'dig')
    await pickupNear(p)
    const after = invSummary(bot)
    const got = []
    for (const [k, v] of Object.entries(after)) {
      const d = v - (before[k] || 0)
      if (d > 0) { got.push(`${k} x${d}`); state.gained[k] = (state.gained[k] || 0) + d }
    }
    if (state.placed.has(key(p))) state.placed.delete(key(p))
    return `dug ${block.name} at ${fmt(p)}` + (got.length ? `, got ${got.join(', ')}` : '')
  }

  async function pickupNear (p) {
    // walk onto nearby dropped items so they get collected
    for (let i = 0; i < 3; i++) {
      await sleep(250)
      const drop = Object.values(bot.entities).find(e => (e.name === 'item' || e.type === 'object' || e.objectType === 'Item' || e.displayName === 'Item') &&
        e.position.distanceTo(p) < 4 && e !== bot.entity)
      if (!drop) return
      try { await withTimeout(bot.pathfinder.goto(new goals.GoalBlock(Math.floor(drop.position.x), Math.floor(drop.position.y), Math.floor(drop.position.z))), 5000, 'pickup') } catch (e) { bot.pathfinder.stop(); return }
    }
  }

  async function collect (block, count = 1) {
    guard()
    count = Math.max(1, Math.min(16, parseInt(count) || 1))
    const t0 = Date.now()
    const results = []
    let done = 0
    const tried = new Set()
    while (done < count && Date.now() - t0 < 60000) {
      guard()
      const cands = nearest(bot, block, 12).filter(c => !tried.has(key(c)))
      if (!cands.length) break
      const c = cands[0]
      tried.add(key(c))
      try {
        const r = await dig(c.x, c.y, c.z)
        results.push(r)
        done++
      } catch (e) {
        if (/night/.test(e.message)) throw e
        results.push(`skip ${fmt(c)}: ${e.message}`)
      }
    }
    return `collected ${done}/${count} ${block} in ${Math.round((Date.now() - t0) / 1000)}s. ` + results.slice(-3).join('; ')
  }

  async function place (item, x, y, z) {
    guard()
    const p = new Vec3(Math.floor(x), Math.floor(y), Math.floor(z))
    const target = bot.blockAt(p)
    if (target && !['air', 'cave_air', 'grass', 'tall_grass', 'water'].includes(target.name)) throw new Error(`${fmt(p)} is occupied by ${target.name}`)
    const it = bot.inventory.items().find(i => i.name === item)
    if (!it) throw new Error(`no ${item} in inventory`)
    const me = bot.entity.position.floored()
    if ((me.x === p.x && me.z === p.z) && (me.y === p.y || me.y + 1 === p.y)) {
      // step aside so we do not place a block inside ourselves
      throw new Error(`${fmt(p)} is where you are standing; move first`)
    }
    if (bot.entity.position.distanceTo(p.offset(0.5, 0.5, 0.5)) > 4) {
      try { await withTimeout(bot.pathfinder.goto(new goals.GoalNear(p.x, p.y, p.z, 3)), 20000, 'walk to place') } catch (e) { bot.pathfinder.stop(); throw new Error(`could not get close to ${fmt(p)}`) }
    }
    const faces = [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]
    let ref = null; let face = null
    for (const f of faces) {
      const b = bot.blockAt(p.offset(...f))
      if (b && b.boundingBox === 'block') { ref = b; face = new Vec3(-f[0], -f[1], -f[2]); break }
    }
    if (!ref) throw new Error(`no solid block next to ${fmt(p)} to place against`)
    await bot.equip(it, 'hand')
    try { await withTimeout(bot.placeBlock(ref, face), 6000, 'place') } catch (e) { /* flying-squid sometimes never confirms; check below */ }
    await sleep(250)
    const now = bot.blockAt(p)
    if (!now || now.name !== item) {
      // the server is the truth; fall back to its world state
      const sb = await serv.overworld.getBlock(p)
      if (!sb || sb.name !== item) throw new Error(`placing ${item} at ${fmt(p)} failed`)
    }
    state.placed.set(key(p), { name: item, by: name })
    return `placed ${item} at ${fmt(p)}`
  }

  async function craft (item, count = 1) {
    guard()
    const r = recipes[item]
    if (!r) throw new Error(`unknown recipe ${item}. Known: ${Object.keys(recipes).join(', ')}`)
    count = Math.max(1, Math.min(16, parseInt(count) || 1))
    if (r.table) {
      const near = [...state.placed.entries()].some(([k, v]) => v.name === 'crafting_table' &&
        new Vec3(...k.split(',').map(Number)).distanceTo(bot.entity.position) <= 5)
      if (!near) throw new Error(`${item} needs a placed crafting_table within 5 blocks`)
    }
    let made = 0
    for (let i = 0; i < count; i++) {
      const missing = Object.entries(r.needs).filter(([k, v]) => invCount(name, k) < v)
      if (missing.length) {
        if (made === 0) throw new Error(`missing ingredients for ${item}: ` + missing.map(([k, v]) => `${k} ${invCount(name, k)}/${v}`).join(', '))
        break
      }
      for (const [k, v] of Object.entries(r.needs)) invTake(name, k, v)
      invGive(name, item, r.makes)
      made++
    }
    state.crafted[item] = (state.crafted[item] || 0) + made * r.makes
    await sleep(400)
    return `crafted ${made * r.makes} ${item}`
  }

  async function say (text) {
    state.board.push({ from: name, text: String(text).slice(0, 200), t: Math.round((Date.now() - state.dayStart) / 1000) })
    bot.chat(String(text).slice(0, 100))
    return 'posted to team board'
  }

  function look () { return status(name) }
  function find (block, count = 5) { return nearest(bot, block, Math.min(10, parseInt(count) || 5)) }
  function blockAt (x, y, z) { const b = bot.blockAt(new Vec3(Math.floor(x), Math.floor(y), Math.floor(z))); return b ? b.name : 'unknown' }
  function position () { const p = bot.entity.position.floored(); return { x: p.x, y: p.y, z: p.z } }
  function inventory () { return invSummary(bot) }
  async function wait (seconds = 1) { guard(); await sleep(Math.min(10, seconds) * 1000); return 'waited' }

  const api = { goto, dig, collect, place, craft, say, look, find, blockAt, position, inventory, wait, isShelter: (x, y, z) => shelter.isShelterAt(bot, x, y, z) }
  if (opts.testMode) api.give = (item, n = 1) => { invGive(name, item, n); return sleep(300) }
  return { api, ctl }
}

// ---------------------------------------------------------------- skills (agent-written JS)
const BANNED = /\b(require|process|import|eval|Function|constructor|globalThis|global|__proto__|prototype|module\.constructor|Reflect|Proxy|WebAssembly|Buffer|child_process)\b/

function loadSkill (source) {
  const stripped = source.replace(/module\.exports/g, 'MODULE_EXPORTS')
  if (BANNED.test(stripped)) throw new Error('skill uses a forbidden identifier: ' + stripped.match(BANNED)[0])
  const sandbox = { module: { exports: {} }, Math, JSON, Number, String, Array, Object: { keys: Object.keys, values: Object.values, entries: Object.entries, assign: Object.assign }, Promise, parseInt, parseFloat, isNaN }
  sandbox.exports = sandbox.module.exports
  vm.runInNewContext(source, sandbox, { timeout: 1000, filename: 'skill.js' })
  const s = sandbox.module.exports
  if (!s || typeof s.run !== 'function' || !s.name || !/^[a-z_][a-z0-9_]*$/.test(s.name)) throw new Error('skill must export {name, description, parameters, run(api,args), test(api)}')
  if (typeof s.test !== 'function') throw new Error('skill must export a test(api) function')
  return s
}

function loadSkills () {
  skills = {}
  if (!SKILLS_DIR || !fs.existsSync(SKILLS_DIR)) return
  for (const f of fs.readdirSync(SKILLS_DIR).filter(f => f.endsWith('.js')).sort()) {
    try {
      const s = loadSkill(fs.readFileSync(path.join(SKILLS_DIR, f), 'utf8'))
      skills[s.name] = s
    } catch (e) { console.error('skill load failed', f, e.message) }
  }
}

async function runSkill (name, skill, args, ms = 90000, testMode = false) {
  const { api, ctl } = makeApi(name, { testMode })
  const p = testMode ? skill.test(api) : skill.run(api, args || {})
  try {
    return await Promise.race([p, sleep(ms).then(() => { ctl.cancelled = true; throw new Error(`skill ${skill.name} timed out after ${ms / 1000}s`) })])
  } finally { ctl.cancelled = true; bots[name].bot.pathfinder.stop() }
}

// ---------------------------------------------------------------- observation + scoring
function status (name) {
  const bot = bots[name].bot
  const me = bot.entity.position.floored()
  const res = {}
  for (const b of ['oak_log', 'stone', 'crafting_table']) res[b] = nearest(bot, b, 3)
  const tables = [...state.placed.entries()].filter(([, v]) => v.name === 'crafting_table').map(([k]) => k)
  return {
    bot: name,
    seconds_left: Math.round(secondsLeft()),
    position: { x: me.x, y: me.y, z: me.z },
    health: bot.health,
    inventory: invSummary(bot),
    nearby: res,
    crafting_tables: tables,
    teammates: Object.keys(bots).filter(n => n !== name).map(n => ({ name: n, position: fmt(bots[n].bot.entity.position), doing: bots[n].last })),
    board: state.board.slice(-5),
    skills: Object.keys(skills)
  }
}

function score () {
  const shelters = shelter.findShelters(state.placed, (p) => {
    const anyBot = Object.values(bots)[0].bot
    const b = anyBot.blockAt(p)
    return b ? b.name : 'unknown'
  })
  const inside = Object.keys(bots).filter(n => shelters.some(s => shelter.inside(bots[n].bot.entity.position, s)))
  const g = (k) => state.gained[k] || 0
  const c = (k) => state.crafted[k] || 0
  const placedCount = (n) => [...state.placed.values()].filter(v => v.name === n).length
  const quests = [
    { id: 'logs', desc: 'Gather oak logs (1 pt each, max 12)', points: Math.min(12, g('oak_log')), max: 12 },
    { id: 'planks', desc: 'Craft oak planks', points: c('oak_planks') > 0 ? 5 : 0, max: 5 },
    { id: 'table', desc: 'Craft and place a crafting table', points: placedCount('crafting_table') > 0 ? 10 : 0, max: 10 },
    { id: 'wooden_pickaxe', desc: 'Craft a wooden pickaxe (at a table)', points: c('wooden_pickaxe') > 0 ? 15 : 0, max: 15 },
    { id: 'cobblestone', desc: 'Mine cobblestone (1 pt each, max 16)', points: Math.min(16, g('cobblestone')), max: 16 },
    { id: 'stone_pickaxe', desc: 'Craft a stone pickaxe (at a table)', points: c('stone_pickaxe') > 0 ? 20 : 0, max: 20 },
    { id: 'shelter', desc: 'Build a shelter: a 1x1, 2-tall air pocket fully walled and roofed with placed planks/cobblestone/logs', points: shelters.length ? 50 : 0, max: 50 },
    { id: 'safe', desc: 'Villagers inside a shelter when night falls (20 each)', points: isNight() || state.final ? 20 * inside.length : 0, max: 20 * Object.keys(bots).length }
  ]
  const total = quests.reduce((a, q) => a + q.points, 0)
  return { total, max: quests.reduce((a, q) => a + q.max, 0), quests, shelters, inside, gained: state.gained, crafted: state.crafted, placed: state.placed.size, seconds_left: Math.round(secondsLeft()) }
}

// ---------------------------------------------------------------- protocol
const PRIMS = new Set(['goto', 'dig', 'collect', 'place', 'craft', 'say', 'find', 'wait'])

async function handle (msg) {
  switch (msg.op) {
    case 'init': {
      await startServer()
      mineflayer = require('mineflayer');
      ({ pathfinder, Movements, goals } = require('mineflayer-pathfinder'))
      loadSkills()
      for (const n of BOTS) await spawnBot(n)
      await sleep(2500) // chunks
      state.started = true
      state.dayStart = Date.now()
      serv.setTime?.(1000)
      return { seed: SEED, port: PORT, bots: BOTS, day_seconds: DAY_SECONDS, skills: Object.entries(skills).map(([k, s]) => ({ name: k, description: s.description, parameters: s.parameters })), recipes }
    }
    case 'status':
      return status(msg.bot)
    case 'act': {
      const b = bots[msg.bot]
      if (!b) throw new Error('unknown bot ' + msg.bot)
      if (isNight() && !TEST_MODE) return { text: 'night has fallen; the day is over', night: true }
      const args = msg.args || {}
      b.busy = true
      b.last = msg.name
      try {
        let text
        if (PRIMS.has(msg.name)) {
          const { api } = makeApi(msg.bot)
          const fn = api[msg.name]
          const ordered = { goto: ['x', 'y', 'z'], dig: ['x', 'y', 'z'], collect: ['block', 'count'], place: ['item', 'x', 'y', 'z'], craft: ['item', 'count'], say: ['text'], find: ['block', 'count'], wait: ['seconds'] }[msg.name]
          const r = await fn(...ordered.map(k => args[k]))
          text = typeof r === 'string' ? r : JSON.stringify(r)
        } else if (skills[msg.name]) {
          const r = await runSkill(msg.bot, skills[msg.name], args)
          text = `skill ${msg.name}: ` + (typeof r === 'string' ? r : JSON.stringify(r))
        } else {
          throw new Error(`unknown action ${msg.name}`)
        }
        event(msg.bot, `${msg.name} ${JSON.stringify(args)} -> ${text.slice(0, 160)}`)
        return { text, night: isNight() }
      } catch (e) {
        event(msg.bot, `${msg.name} ${JSON.stringify(args)} -> ERROR ${e.message.slice(0, 160)}`)
        return { text: 'error: ' + e.message, night: isNight() }
      } finally { b.busy = false }
    }
    case 'score':
      if (msg.final) state.final = true
      return score()
    case 'events':
      return state.events
    case 'test_skill': {
      const s = loadSkill(msg.source)
      const name = BOTS[0]
      const r = await runSkill(name, s, {}, (msg.timeout || 90) * 1000, true)
      return { ok: true, result: r === undefined ? null : r, inventory: invSummary(bots[name].bot), placed: state.placed.size }
    }
    case 'check_skill':
      return { name: loadSkill(msg.source).name }
    case 'shutdown':
      setTimeout(() => process.exit(0), 100)
      return 'bye'
    default:
      throw new Error('unknown op ' + msg.op)
  }
}

const rl = readline.createInterface({ input: process.stdin })
rl.on('line', (line) => {
  if (!line.trim()) return
  let msg
  try { msg = JSON.parse(line) } catch (e) { send({ id: null, ok: false, error: 'bad json' }); return }
  handle(msg).then(
    (result) => send({ id: msg.id, ok: true, result }),
    (e) => send({ id: msg.id, ok: false, error: String(e && e.message ? e.message : e) })
  )
})
rl.on('close', () => process.exit(0))
process.on('uncaughtException', (e) => console.error('uncaught', e && e.stack))
process.on('unhandledRejection', (e) => console.error('unhandled', e && e.stack ? e.stack : e))
void realLog

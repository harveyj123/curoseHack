// Seeded terrain for Sleep On It: diamond-square hills + oak trees + surface stone outcrops.
// flying-squid loads this by absolute path (generation.name), so the world is fully ours and reproducible.
const Vec3 = require('vec3').Vec3
const diamondSquare = require('diamond-square')
const rand = require('random-seed')

module.exports = function generation (opts) {
  const { registry, seed } = opts
  const base = diamondSquare({ ...opts, worldHeight: 70, waterline: 20 })
  const B = registry.blocksByName
  const log = B.oak_log.defaultState
  const leaves = B.oak_leaves.defaultState
  const stone = B.stone.defaultState
  const grass = B.grass_block.id
  const air = B.air.defaultState

  return function (chunkX, chunkZ) {
    const chunk = base(chunkX, chunkZ)
    const r = rand.create(`trees:${seed}:${chunkX}:${chunkZ}`)
    const surface = (x, z) => {
      for (let y = 90; y > 1; y--) {
        const t = chunk.getBlockType(new Vec3(x, y, z))
        if (t === grass) return y
        if (t !== B.air.id && t !== B.grass.id && t !== B.tall_grass?.id) return -1
      }
      return -1
    }
    const nTrees = 1 + r(3)
    for (let i = 0; i < nTrees; i++) {
      const x = 2 + r(12); const z = 2 + r(12)
      const y = surface(x, z)
      if (y < 0) continue
      const h = 4 + r(2)
      for (let dy = h - 2; dy <= h + 1; dy++) {
        const rad = dy >= h ? 1 : 2
        for (let dx = -rad; dx <= rad; dx++) {
          for (let dz = -rad; dz <= rad; dz++) {
            if (Math.abs(dx) === rad && Math.abs(dz) === rad && r(2) === 0) continue
            const p = new Vec3(x + dx, y + dy, z + dz)
            if (chunk.getBlockStateId(p) === air) chunk.setBlockStateId(p, leaves)
          }
        }
      }
      for (let dy = 1; dy <= h; dy++) chunk.setBlockStateId(new Vec3(x, y + dy, z), log)
    }
    if (r(3) === 0) { // a boulder of stone sitting on the grass
      const x = 3 + r(10); const z = 3 + r(10)
      const y = surface(x, z)
      if (y > 0) {
        for (let dx = 0; dx < 2; dx++) for (let dz = 0; dz < 2; dz++) for (let dy = 1; dy <= 2; dy++) {
          if (dy === 2 && (dx + dz) % 2) continue
          chunk.setBlockStateId(new Vec3(x + dx, y + dy, z + dz), stone)
        }
      }
    }
    return chunk
  }
}

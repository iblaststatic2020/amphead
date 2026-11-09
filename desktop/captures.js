// Turning a flat capture list into the shape a player thinks in: brand, then model, then the
// channel or flavour the file names already carry (clean, crunch, red, lead...). This is pure
// data shaping with no React in it, so it can be tested on its own and the face stays a view.
//
// The folder is the strongest signal the library has: "Peavey 6505" is how these files were
// collected. When the remainder after the brand contains a number (6505, MT100, SLO-100), it is
// treated as the model. Known brands split even without a number ("Splawn Quick Rod").

const BRANDS = new Set([
  'peavey', 'prs', 'mesa', 'soldano', 'splawn', 'bugera', 'revv', 'marshall', 'fender', 'vox',
  'orange', 'evh', 'friedman', 'diezel', 'engl', 'bogner', 'hughes', 'korg', 'yamaha', 'randall'
])

// Order matters: the first word found wins, so "crunch" is chosen before a generic "drive" in
// "Crunch-NoDrive", and "highgain" before "gain".
const VARIANTS = [
  'clean', 'crunch', 'red', 'blue', 'green', 'purple', 'lead', 'rhythm', 'highgain', 'lowgain',
  'edgebrkp', 'overdrive', 'aggr1', 'aggr0', 'drive', 'gain'
]

const VARIANT_LABEL = {
  highgain: 'HIGH GAIN',
  lowgain: 'LOW GAIN',
  edgebrkp: 'EDGE OF BREAKUP',
  aggr0: 'AGGRESSIVE 0',
  aggr1: 'AGGRESSIVE 1'
}

const tidy = (text) => String(text || '').replace(/[_\s]+/g, ' ').trim()
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

export function familyOf(folder) {
  const words = tidy(folder).split(' ').filter(Boolean)
  if (!words.length) return { brand: 'OTHER', model: '' }
  const rest = words.slice(1).join(' ')
  if (rest && (/\d/.test(rest) || BRANDS.has(words[0].toLowerCase()))) {
    return { brand: words[0], model: rest }
  }
  return { brand: words.join(' '), model: '' }
}

export function variantOf(name) {
  const tokens = String(name || '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
  return VARIANTS.find((word) => tokens.includes(word)) || ''
}

// The row under a variant should not repeat what the group headers already say. Strip the
// "[AMP]" tag, the brand and model words, and the chosen variant word, then dress separators as
// middots. If that eats everything, keep the original name rather than showing a blank row.
export function shortName(name, brand, model, variant) {
  let out = String(name || '').replace(/^\[[^\]]*\]\s*/, '')
  const kill = [brand, ...String(model || '').split(/\s+/), variant].filter(Boolean)
  for (const word of kill) {
    const re = new RegExp(`(^|[\\s\\-_,])${escape(word)}(?=$|[\\s\\-_,])`, 'i')
    out = out.replace(re, '$1')
  }
  out = out.split(/[-_]/)
    .map((part) => part.replace(/^[\s,.:]+|[\s,.:]+$/g, '').replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join(' · ')
  return out || String(name || '')
}

export function captureTree(items) {
  const brands = new Map()
  for (const file of items || []) {
    const { brand, model } = familyOf(file.folder)
    const variant = variantOf(file.name)
    const brandKey = brand.toLowerCase()
    if (!brands.has(brandKey)) brands.set(brandKey, { key: brandKey, label: brand, models: new Map() })
    const brandNode = brands.get(brandKey)
    const modelKey = model.toLowerCase() || '_'
    if (!brandNode.models.has(modelKey)) {
      brandNode.models.set(modelKey, { key: modelKey, label: model || brand, items: [], buckets: new Map() })
    }
    const modelNode = brandNode.models.get(modelKey)
    const bucketKey = variant || '_'
    if (!modelNode.buckets.has(bucketKey)) {
      modelNode.buckets.set(bucketKey, {
        key: bucketKey,
        label: variant ? (VARIANT_LABEL[variant] || variant.toUpperCase()) : 'OTHER',
        order: variant ? VARIANTS.indexOf(variant) : 999,
        items: []
      })
    }
    modelNode.buckets.get(bucketKey).items.push({
      ...file,
      label: shortName(file.name, brand, model, variant)
    })
  }

  const tree = []
  for (const brandNode of brands.values()) {
    const models = []
    for (const modelNode of brandNode.models.values()) {
      const buckets = [...modelNode.buckets.values()]
        .sort((a, b) => a.order - b.order)
        .map((bucket) => ({ ...bucket, count: bucket.items.length }))
      const variants = buckets.length > 1 ? buckets : []
      models.push({
        key: `${brandNode.key}>${modelNode.key}`,
        label: modelNode.label,
        count: buckets.reduce((sum, bucket) => sum + bucket.items.length, 0),
        // One variant is not a menu, it is just the list. Only make the player open a level
        // that actually offers a choice.
        items: variants.length ? [] : buckets[0].items,
        variants
      })
    }
    models.sort((a, b) => a.label.localeCompare(b.label))
    tree.push({
      key: brandNode.key,
      label: brandNode.label,
      count: models.reduce((sum, model) => sum + model.count, 0),
      models
    })
  }
  tree.sort((a, b) => a.label.localeCompare(b.label))
  return tree
}

import re

with open("game.js", "r", encoding="utf-8") as f:
    text = f.read()

# === 1. TROUVER LE WORKER ===
marker = 'const AI_WORKER_CODE = "'
idx = text.find(marker)
line_end = text.find('\n', idx)
worker_line = text[idx:line_end]

prefix = worker_line[:24]  # 'const AI_WORKER_CODE = "'
worker_content = worker_line[24:-2]  # enlève le " final et le ;
worker = worker_content.replace('\\n', '\n')

# === 2. PATCHES SUR LE WORKER ===

# 2a. Zobrist Hashing
old = 'let MEMORY_PRIORS = {};\\n\\nself.onmessage'
new = '''let MEMORY_PRIORS = {};
let ZOBRIST = { pieces: {}, turn: {}, ranking: {} };
let CURRENT_HASH = 0;

function initZobrist() {
  const rand = () => Math.floor(Math.random() * 0x7FFFFFFF);
  for (const piece of state.pieces) {
    if (!ZOBRIST.pieces[piece.id]) ZOBRIST.pieces[piece.id] = {};
    for (const cell of CELL_IDS) {
      if (!ZOBRIST.pieces[piece.id][cell]) ZOBRIST.pieces[piece.id][cell] = {};
      ZOBRIST.pieces[piece.id][cell]["RECTO"] = rand();
      ZOBRIST.pieces[piece.id][cell]["VERSO"] = rand();
    }
  }
  for (const p of state.players) ZOBRIST.turn[p.id] = rand();
  CURRENT_HASH = 0;
  for (const piece of state.pieces) {
    CURRENT_HASH ^= ZOBRIST.pieces[piece.id][piece.position][piece.face];
  }
  for (const entry of (state.ranking || [])) {
    if (!ZOBRIST.ranking[entry.playerId]) ZOBRIST.ranking[entry.playerId] = rand();
    CURRENT_HASH ^= ZOBRIST.ranking[entry.playerId];
  }
}

function updateZobristMove(piece, from, to, oldFace, newFace) {
  CURRENT_HASH ^= ZOBRIST.pieces[piece.id][from][oldFace];
  CURRENT_HASH ^= ZOBRIST.pieces[piece.id][to][newFace];
}

function boardHash(playerToMove, depth, aiPlayer) {
  return (CURRENT_HASH ^ (depth * 0x9E3779B9) ^ (playerToMove ? ZOBRIST.turn[playerToMove.id] : 0)) >>> 0;
}

self.onmessage'''
worker = worker.replace(old, new, 1)

# 2b. applyTemporaryMove avec hash
old = '''function applyTemporaryMove(player, piece, to) {
const undo = {
piece,
previousPosition: piece.position,
previousFace: piece.face,
};
const previousFace = piece.face;
const from = piece.position;
piece.position = to;
piece.face = piece.face === "RECTO" ? "VERSO" : "RECTO";'''
new = '''function applyTemporaryMove(player, piece, to) {
const undo = {
piece,
previousPosition: piece.position,
previousFace: piece.face,
hash: CURRENT_HASH
};
const previousFace = piece.face;
const from = piece.position;
const newFace = piece.face === "RECTO" ? "VERSO" : "RECTO";
updateZobristMove(piece, from, to, previousFace, newFace);
piece.position = to;
piece.face = newFace;'''
worker = worker.replace(old, new, 1)

# 2c. undoTemporaryMove
old = '''function undoTemporaryMove(undo) {
state.moveHistory.pop();
undo.piece.position = undo.previousPosition;
undo.piece.face = undo.previousFace;
}'''
new = '''function undoTemporaryMove(undo) {
state.moveHistory.pop();
undo.piece.position = undo.previousPosition;
undo.piece.face = undo.previousFace;
CURRENT_HASH = undo.hash;
}'''
worker = worker.replace(old, new, 1)

# 2d. initZobrist dans onmessage
old = 'SEARCH_DEADLINE = Date.now() + Math.max(250, (CONFIG.maxDecisionMs || 6000) - 300);\\nSEARCH_NODES = 0;\\nSEARCH_REACHED_DEPTH = 0;\\n\\nconst player'
new = 'SEARCH_DEADLINE = Date.now() + Math.max(250, (CONFIG.maxDecisionMs || 6000) - 300);\\nSEARCH_NODES = 0;\\nSEARCH_REACHED_DEPTH = 0;\\ninitZobrist();\\n\\nconst player'
worker = worker.replace(old, new, 1)

# 2e. Alléger evaluatePlayerPosition (retirer mobility, réduire winningPattern)
worker = worker.replace('const mobility = legalMovesForPlayer(player).length;\\n', '')
worker = worker.replace('score += mobility * 7;\\n', '')
worker = worker.replace('score += winningPatternScore(player);', 'score += winningPatternScore(player) * 0.3;')

# 2f. orderMovesForSearch rapide
old = '''function orderMovesForSearch(player, moves, aiPlayer) {
const ordered = [...moves].sort((a, b) => {
const scoreA = staticMoveScore(player, a.piece, a.to, aiPlayer) + (player.id === aiPlayer.id ? memoryPriorFor(a) : 0);
const scoreB = staticMoveScore(player, b.piece, b.to, aiPlayer) + (player.id === aiPlayer.id ? memoryPriorFor(b) : 0);

// Pour l'IA elle-même : meilleurs scores d'abord.
// Pour un adversaire simulé : coups les plus dangereux pour l'IA d'abord.
return player.id === aiPlayer.id ? scoreB - scoreA : scoreA - scoreB;
});

return ordered;
}'''
new = '''function quickMoveScore(player, move, aiPlayer) {
let score = 0;
const piece = move.piece;
const to = move.to;
if (moveWinsImmediately(piece, to)) return 99999999;
const neighbours = SIDE_ADJACENCY_MAP[to] || [];
let connects = 0, sameFaceConnects = 0;
for (const n of neighbours) {
const other = pieceAt(n);
if (other && other.color === player.color) {
connects++;
if (other.face === piece.face) sameFaceConnects++;
}
}
score += connects * 500 + sameFaceConnects * 800;
const cTo = CELL_CENTROIDS[to];
const cFrom = CELL_CENTROIDS[piece.position];
if (cTo && cFrom) {
const distFrom = Math.abs(cFrom.x - 300) + Math.abs(cFrom.y - 300);
const distTo = Math.abs(cTo.x - 300) + Math.abs(cTo.y - 300);
score += (distFrom - distTo) * 2;
}
if (player.id === aiPlayer.id) {
const undo = applyTemporaryMove(player, piece, to);
if (countImmediateWinningMoves(player) > 0) score += 200000;
undoTemporaryMove(undo);
}
if (isImmediateReturn(piece, to)) score -= 10000;
return score;
}

function orderMovesForSearch(player, moves, aiPlayer) {
const scored = new Array(moves.length);
for (let i = 0; i < moves.length; i++) {
const m = moves[i];
scored[i] = { move: m, score: quickMoveScore(player, m, aiPlayer) + (player.id === aiPlayer.id ? memoryPriorFor(m) : 0) };
}
scored.sort((a, b) => player.id === aiPlayer.id ? b.score - a.score : a.score - b.score);
return scored.map(s => s.move);
}'''
worker = worker.replace(old, new, 1)

# 2g. chooseAIMove avec Iterative Deepening
old = '''function chooseAIMove(player) {
const moves = legalMovesForPlayer(player);
if (!moves.length) return null;

// 1. Victoire immédiate.
const winningMove = moves.find(move => wouldWinAfterMove(player, move.piece, move.to));
if (winningMove) return winningMove;

// 2. Blocage temporaire stratégique si un adversaire menace immédiatement.
const blockers = immediateBlockMoves(player, moves);
const strategicBase = blockers.length ? blockers : openingMovePool(player, moves);

const depth = aiSearchDepth();
const cache = new Map();
const ordered = limitMovesForSearch(orderMovesForSearch(player, strategicBase, player));

let bestMove = ordered[0];
let bestScore = -Infinity;
let alpha = -Infinity;
const beta = Infinity;

for (const move of ordered) {
const undo = applyTemporaryMove(player, move.piece, move.to);
const score = minimax(nextSearchPlayer(player), depth - 1, alpha, beta, player, cache);
undoTemporaryMove(undo);

const tieBreak =
staticMoveScore(player, move.piece, move.to, player) +
centralConnectionScore(player, move);

const bestTieBreak =
staticMoveScore(player, bestMove.piece, bestMove.to, player) +
centralConnectionScore(player, bestMove);

if (score > bestScore || (score === bestScore && tieBreak > bestTieBreak)) {
bestScore = score;
bestMove = move;
}

alpha = Math.max(alpha, bestScore);
}

return bestMove;
}'''
new = '''function chooseAIMove(player) {
const moves = legalMovesForPlayer(player);
if (!moves.length) return null;

const winningMove = moves.find(move => wouldWinAfterMove(player, move.piece, move.to));
if (winningMove) return winningMove;

const blockers = immediateBlockMoves(player, moves);
const strategicBase = blockers.length ? blockers : openingMovePool(player, moves);
const profile = adaptiveSearchProfile(player);
let bestMove = strategicBase[0] || moves[0];
let bestScore = -Infinity;

for (let depth = 1; depth <= profile.depth; depth++) {
if (searchTimeExpired()) break;
let currentBest = null;
let currentBestScore = -Infinity;
let alpha = -Infinity;
const beta = Infinity;
const ordered = orderMovesForSearch(player, strategicBase, player);
if (bestMove && depth > 1) {
const pvIdx = ordered.findIndex(m => m.piece.id === bestMove.piece.id && m.to === bestMove.to);
if (pvIdx > 0) { ordered.splice(pvIdx, 1); ordered.unshift(bestMove); }
}
for (const move of ordered) {
if (searchTimeExpired()) break;
const undo = applyTemporaryMove(player, move.piece, move.to);
const score = minimax(nextSearchPlayer(player), depth - 1, alpha, beta, player, profile);
undoTemporaryMove(undo);
if (score > currentBestScore) {
currentBestScore = score;
currentBest = move;
}
alpha = Math.max(alpha, currentBestScore);
}
if (currentBest && !searchTimeExpired()) {
bestMove = currentBest;
bestScore = currentBestScore;
SEARCH_REACHED_DEPTH = depth;
} else {
break;
}
}
return bestMove;
}'''
worker = worker.replace(old, new, 1)

# 2h. minimax allégé (sans paramètre cache inutilisé)
old = '''function minimax(playerToMove, depth, alpha, beta, aiPlayer, cache, profile) {
SEARCH_NODES++;
if ((SEARCH_NODES & 63) === 0 && searchTimeExpired()) return evaluateBoardForAI(aiPlayer);
const cacheKey = boardHash(playerToMove, depth, aiPlayer);
const cached = cache.get(cacheKey);
if (cached && cached.depth >= depth) return cached.score;
if (checkVictory(aiPlayer)) return CONFIG.winScore + depth;

const opponents = state.players.filter(p => p.id !== aiPlayer.id && !isPlayerRanked(p.id));
if (opponents.some(opponent => checkVictory(opponent))) return -CONFIG.winScore - depth;

if (depth <= 0) {
const value = quiescenceValue(aiPlayer); cache.set(cacheKey, { depth, score: value }); return value;
}

if (!playerToMove || isPlayerRanked(playerToMove.id)) return minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, cache, profile);

const moves = legalMovesForPlayer(playerToMove);

if (!moves.length) return minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, cache, profile);

const branchLimit = Math.max(4, profile.limit - Math.floor((profile.depth - depth) * 1.5));
const ordered = mergeAndLimitMoves(playerToMove, moves, aiPlayer, branchLimit);
const maximizing = playerToMove.id === aiPlayer.id;

if (maximizing) {
let value = -Infinity;

for (const move of ordered) {
if (searchTimeExpired()) break;
const undo = applyTemporaryMove(playerToMove, move.piece, move.to);
value = Math.max(value, minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, cache, profile));
undoTemporaryMove(undo);

alpha = Math.max(alpha, value);
if (alpha >= beta) break;
}

cache.set(cacheKey, { depth, score: value });
return value;
}

let value = Infinity;

for (const move of ordered) {
if (searchTimeExpired()) break;
const undo = applyTemporaryMove(playerToMove, move.piece, move.to);
value = Math.min(value, minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, cache, profile));
undoTemporaryMove(undo);

beta = Math.min(beta, value);
if (alpha >= beta) break;
}

cache.set(cacheKey, { depth, score: value });
return value;
}'''
new = '''function minimax(playerToMove, depth, alpha, beta, aiPlayer, profile) {
SEARCH_NODES++;
if ((SEARCH_NODES & 63) === 0 && searchTimeExpired()) return evaluateBoardForAI(aiPlayer);
if (checkVictory(aiPlayer)) return CONFIG.winScore + depth;
const opponents = state.players.filter(p => p.id !== aiPlayer.id && !isPlayerRanked(p.id));
for (const opp of opponents) { if (checkVictory(opp)) return -CONFIG.winScore - depth; }
if (depth <= 0) return quiescenceValue(aiPlayer);
if (!playerToMove || isPlayerRanked(playerToMove.id)) return minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, profile);
const moves = legalMovesForPlayer(playerToMove);
if (!moves.length) return minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, profile);
const branchLimit = Math.max(4, profile.limit - Math.floor((profile.depth - depth) * 1.5));
const ordered = mergeAndLimitMoves(playerToMove, moves, aiPlayer, branchLimit).slice(0, branchLimit);
const maximizing = playerToMove.id === aiPlayer.id;
let value = maximizing ? -Infinity : Infinity;
for (const move of ordered) {
if ((SEARCH_NODES & 63) === 0 && searchTimeExpired()) break;
const undo = applyTemporaryMove(playerToMove, move.piece, move.to);
const child = minimax(nextSearchPlayer(playerToMove), depth - 1, alpha, beta, aiPlayer, profile);
undoTemporaryMove(undo);
if (maximizing) {
value = Math.max(value, child);
alpha = Math.max(alpha, value);
} else {
value = Math.min(value, child);
beta = Math.min(beta, value);
}
if (alpha >= beta) break;
}
if (!Number.isFinite(value)) value = evaluateBoardForAI(aiPlayer);
return value;
}'''
worker = worker.replace(old, new, 1)

# 2i. Optimiser averagePairDistance
old = '''function averagePairDistance(pieces) {
if (pieces.length <= 1) return 0;

let total = 0;
let count = 0;

for (let i = 0; i < pieces.length; i++) {
for (let j = i + 1; j < pieces.length; j++) {
const a = centroidOf(polygonForCell(pieces[i].position));
const b = centroidOf(polygonForCell(pieces[j].position));
total += Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
count++;
}
}

return total / count;
}'''
new = '''function averagePairDistance(pieces) {
if (pieces.length <= 1) return 0;
let total = 0;
let count = 0;
for (let i = 0; i < pieces.length; i++) {
const a = CELL_CENTROIDS[pieces[i].position];
if (!a) continue;
for (let j = i + 1; j < pieces.length; j++) {
const b = CELL_CENTROIDS[pieces[j].position];
if (!b) continue;
total += Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
count++;
}
}
return count ? total / count : 0;
}'''
worker = worker.replace(old, new, 1)

# 2j. Optimiser centerControlScore
old = '''function centerControlScore(pieces) {
const center = { x: 300, y: 300 };
return pieces.reduce((score, piece) => {
const c = centroidOf(polygonForCell(piece.position));
return score + Math.max(0, 350 - (Math.abs(c.x - center.x) + Math.abs(c.y - center.y)));
}, 0) / 100;
}'''
new = '''function centerControlScore(pieces) {
const cx = 300, cy = 300;
let score = 0;
for (const piece of pieces) {
const c = CELL_CENTROIDS[piece.position];
if (!c) continue;
score += Math.max(0, 350 - (Math.abs(c.x - cx) + Math.abs(c.y - cy)));
}
return score / 100;
}'''
worker = worker.replace(old, new, 1)

# === 3. RECONSTRUIRE LE FICHIER ===
worker_encoded = worker.replace('\n', '\\n')
new_worker_line = prefix + worker_encoded + '";'
new_text = text[:idx] + new_worker_line + text[line_end:]

# === 4. PATCHES SUR LE CODE PRINCIPAL (après le worker) ===
# Alléger evaluatePlayerPosition principal
marker2 = 'const blob = new Blob([AI_WORKER_CODE]'
split_idx = new_text.find(marker2)
before = new_text[:split_idx]
after = new_text[split_idx:]

after = after.replace('const mobility = legalMovesForPlayer(player).length;\\n', '')
after = after.replace('score += mobility * 7;\\n', '')
after = after.replace('score += winningPatternScore(player);', 'score += winningPatternScore(player) * 0.3;')

# boardHash principal plus rapide
old_bh = '''function boardHash(playerToMove, depth, aiPlayer) {
const pieces = state.pieces
.map(p => `${p.id}:${p.position}:${p.face}`)
.sort()
.join("|");

const lastMoves = state.moveHistory
.filter(move => !move.pass && !move.system)
.slice(-10)
.map(move => `${move.playerId}:${move.pieceId}:${move.from}>${move.to}`)
.join(",");

return `${aiPlayer.id}|${playerToMove?.id || "none"}|${depth}|${pieces}|${lastMoves}`;
}'''
new_bh = '''function boardHash(playerToMove, depth, aiPlayer) {
let h = 0;
for (const p of state.pieces) {
h = (h * 31 + p.position.charCodeAt(0) * 17 + p.position.charCodeAt(1) * 13 + (p.face === "RECTO" ? 1 : 0)) >>> 0;
}
h = (h ^ (depth * 0x9E3779B9) ^ (playerToMove ? playerToMove.id.charCodeAt(1) * 0x1234567 : 0)) >>> 0;
return `${aiPlayer.id}|${h}`;
}'''
after = after.replace(old_bh, new_bh)

# === 5. AUGMENTER LES PROFONDEURS ===
after = after.replace('const AI_SEARCH_DEPTH_2_PLAYERS = 5;', 'const AI_SEARCH_DEPTH_2_PLAYERS = 7;')
after = after.replace('const AI_SEARCH_DEPTH_MULTI = 3;', 'const AI_SEARCH_DEPTH_MULTI = 4;')
after = after.replace('tacticalDepthTwoPlayers: 7,', 'tacticalDepthTwoPlayers: 9,')
after = after.replace('criticalDepthTwoPlayers: 9,', 'criticalDepthTwoPlayers: 11,')
after = after.replace('tacticalDepthMulti: 4,', 'tacticalDepthMulti: 5,')
after = after.replace('criticalDepthMulti: 5,', 'criticalDepthMulti: 6,')

new_text = before + after

# === 6. SAUVEGARDER ===
with open("game.js", "w", encoding="utf-8") as f:
    f.write(new_text)

print("✅ game.js patché avec succès !")
print("Modifications appliquées :")
print("  • Zobrist Hashing (hash O(1) au lieu de O(n log n))")
print("  • Évaluation allégée (pas de recalcul mobilité/patterns lourds)")
print("  • Move Ordering rapide (sans simulation complète)")
print("  • Iterative Deepening (coup toujours dispo, profondeur adaptative)")
print("  • Profondeurs augmentées : 7/9/11 en duel, 4/5/6 en multi")

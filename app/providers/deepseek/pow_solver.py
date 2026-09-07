"""
OmniBridge – DeepSeek PoW Challenge Solver
Implements the 23-round Keccak-f[1600] permutation algorithm (DeepSeekHashV1)
for automated Proof-of-Work authentication against chat.deepseek.com.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("omnibridge.deepseek.pow")

# ─────────────────────────────────────────────────────────────────────────────
# Keccak-f[1600] Constants & Lookup Tables
# ─────────────────────────────────────────────────────────────────────────────

MASK64 = 0xFFFFFFFFFFFFFFFF

# Round constants (24 in standard Keccak; DeepSeekHashV1 runs rounds 1 to 23)
RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

RHO = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _rot64(x: int, n: int) -> int:
    """64-bit circular left shift."""
    n = n % 64
    if n == 0:
        return x & MASK64
    return (((x << n) & MASK64) | (x >> (64 - n))) & MASK64


def _keccak_p_1600_23_rounds(state: list[int]) -> None:
    """
    Execute 23 rounds of Keccak-f[1600] permutation (rounds 1 to 23, skipping round 0).
    Modifies the 25-element 64-bit word state in-place.
    """
    for round_idx in range(1, 24):
        # 1. Theta step
        c0 = state[0] ^ state[5] ^ state[10] ^ state[15] ^ state[20]
        c1 = state[1] ^ state[6] ^ state[11] ^ state[16] ^ state[21]
        c2 = state[2] ^ state[7] ^ state[12] ^ state[17] ^ state[22]
        c3 = state[3] ^ state[8] ^ state[13] ^ state[18] ^ state[23]
        c4 = state[4] ^ state[9] ^ state[14] ^ state[19] ^ state[24]

        d0 = c4 ^ _rot64(c1, 1)
        d1 = c0 ^ _rot64(c2, 1)
        d2 = c1 ^ _rot64(c3, 1)
        d3 = c2 ^ _rot64(c4, 1)
        d4 = c3 ^ _rot64(c0, 1)

        state[0] ^= d0
        state[5] ^= d0
        state[10] ^= d0
        state[15] ^= d0
        state[20] ^= d0

        state[1] ^= d1
        state[6] ^= d1
        state[11] ^= d1
        state[16] ^= d1
        state[21] ^= d1

        state[2] ^= d2
        state[7] ^= d2
        state[12] ^= d2
        state[17] ^= d2
        state[22] ^= d2

        state[3] ^= d3
        state[8] ^= d3
        state[13] ^= d3
        state[18] ^= d3
        state[23] ^= d3

        state[4] ^= d4
        state[9] ^= d4
        state[14] ^= d4
        state[19] ^= d4
        state[24] ^= d4

        # 2. Rho & Pi steps
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rot64(state[x + 5 * y], RHO[x][y])

        # 3. Chi step
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = (
                    b[x + 5 * y] ^ ((~b[((x + 1) % 5) + 5 * y]) & b[((x + 2) % 5) + 5 * y])
                ) & MASK64

        # 4. Iota step
        state[0] ^= RC[round_idx]


def deepseek_hash_v1(data: bytes) -> bytes:
    """
    Compute DeepSeekHashV1 on input bytes.
    Uses SHA3-256 rate (136 bytes), padding suffix 0x06, and 23-round permutation.
    Returns 32-byte hash digest.
    """
    rate = 136
    state = [0] * 25

    # Pad data with SHA3-256 rule: 0x06 ... 0x80
    data_len = len(data)
    padded = bytearray(data)
    padded.append(0x06)
    while len(padded) % rate != (rate - 1):
        padded.append(0x00)
    padded.append(0x80)

    # Process block by block
    for block_start in range(0, len(padded), rate):
        block = padded[block_start : block_start + rate]
        words = struct.unpack("<17Q", block)
        for i in range(17):
            state[i] ^= words[i]
        _keccak_p_1600_23_rounds(state)

    # Output 32 bytes (first 4 words)
    return struct.pack("<4Q", state[0], state[1], state[2], state[3])


# ─────────────────────────────────────────────────────────────────────────────
# Proof-of-Work Challenge Solver
# ─────────────────────────────────────────────────────────────────────────────

def solve_pow_challenge(challenge_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Solve a DeepSeek PoW challenge.
    challenge_data format:
      {
        "algorithm": "DeepSeekHashV1",
        "challenge": "<hex>",
        "salt": "<salt>",
        "difficulty": 14000,
        "expire_at": 1740000000,
        "signature": "<sig>",
        "target_path": "/api/v0/chat/completion"
      }
    """
    salt = challenge_data.get("salt", "")
    expire_at = challenge_data.get("expire_at", int(time.time() + 300))
    difficulty = int(challenge_data.get("difficulty", 10000))
    challenge = challenge_data.get("challenge", "")
    signature = challenge_data.get("signature", "")
    target_path = challenge_data.get("target_path", "/api/v0/chat/completion")

    prefix = f"{salt}_{expire_at}_"
    prefix_bytes = prefix.encode("utf-8")

    # Target threshold: hash (big-endian 256-bit integer) * difficulty < 2^256
    target = (1 << 256) // difficulty if difficulty > 0 else (1 << 256) - 1

    answer = 0
    max_iterations = 2_000_000

    while answer < max_iterations:
        candidate_bytes = prefix_bytes + str(answer).encode("ascii")
        digest = deepseek_hash_v1(candidate_bytes)
        val = int.from_bytes(digest, "big")
        if val <= target:
            logger.debug(
                "Solved DeepSeek PoW for %s (difficulty=%d) in %d iterations.",
                target_path,
                difficulty,
                answer,
            )
            return {
                "algorithm": "DeepSeekHashV1",
                "challenge": challenge,
                "salt": salt,
                "answer": answer,
                "signature": signature,
                "target_path": target_path,
            }
        answer += 1

    logger.warning("DeepSeek PoW hit max iterations (%d). Returning last answer.", max_iterations)
    return {
        "algorithm": "DeepSeekHashV1",
        "challenge": challenge,
        "salt": salt,
        "answer": answer,
        "signature": signature,
        "target_path": target_path,
    }


def encode_pow_response(solution: Dict[str, Any]) -> str:
    """Encode the solution dict into Base64 for the x-ds-pow-response header."""
    json_bytes = json.dumps(solution, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(json_bytes).decode("ascii")


async def get_and_solve_pow(
    session: Any,
    user_token: str,
    target_path: str = "/api/v0/chat/completion",
) -> Optional[str]:
    """
    Request a new PoW challenge from DeepSeek and solve it asynchronously.
    Returns the Base64 encoded x-ds-pow-response header string, or None on failure.
    """
    url = "https://chat.deepseek.com/api/v0/chat/create_pow_challenge"
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
        "x-app-version": "20241129.1",
        "x-client-version": "1.0.0-always",
        "x-client-platform": "web",
        "x-client-locale": "zh_CN",
    }
    payload = {"target_path": target_path}

    try:
        resp = await session.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            biz_data = data.get("data", {}).get("biz_data", {})
            if biz_data and biz_data.get("algorithm") == "DeepSeekHashV1":
                solution = await asyncio.to_thread(solve_pow_challenge, biz_data)
                return encode_pow_response(solution)
        logger.warning(
            "Failed to get DeepSeek PoW challenge (HTTP %d): %s",
            resp.status_code,
            resp.text[:200] if hasattr(resp, "text") else "",
        )
    except Exception as e:
        logger.warning("Error getting/solving DeepSeek PoW challenge: %s", e)

    return None

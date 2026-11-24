import hashlib
from dataclasses import dataclass
from typing import Iterable, Union, List, Dict, Tuple, Optional
import numpy as np


@dataclass(frozen=True)
class SingleQubitRB:
    """
    Single-qubit Clifford-24 RB sequence generator.

    """
    targets: Iterable[Union[int, str]]
    tag: str = "rb_v1"
    add_measure: bool = True
    include_inverse: bool = True

    # IRB options
    mode: str = "rb"  # "rb" or "irb"
    interleaved: Optional[Union[int, str]] = None
    
    # --- public API ---------------------------------------------------------

    def derive_seed(
        self,
        run_seed: Union[int, str],
        m: int,
        k: int
    ) -> int:
        t_str = ",".join(str(x) for x in self.targets)
        msg = f"{self.tag}|mode={self.mode}|run_seed={run_seed}|m={m}|k={k}|targets={t_str}".encode("utf-8")
        digest = hashlib.sha256(msg).digest()
        return int.from_bytes(digest[:8], byteorder="little", signed=False)

    def rb_sequence(
        self,
        m: int,
        k: int, 
        run_seed: Union[int, str],
    ):
        """
        Return a RB Clifford index sequence.

        - m: number of random Cliffords (excluding the final inverse)
        """
        
        if self.mode not in ("rb", "irb"):
            raise ValueError(f"Unsupported mode={self.mode}, expected 'rb' or 'irb'.")

        if self.mode == "irb" and self.interleaved is None:
            raise ValueError("IRB requires `interleaved` gate/clifford to be provided.")

        
        seed = self.derive_seed(run_seed=run_seed, m=m, k=k)
        rng = np.random.Generator(np.random.PCG64(seed))
        rand_idxs = rng.integers(0, 24, size=m).tolist()

        if self.mode == "rb":
            idxs = list(rand_idxs)
            if self.include_inverse:
                now = 0
                for c in idxs:
                    now = self.compose(c, now)
                idxs.append(self.inverse(now))
            return self.get_gates(idxs=idxs)
        
        # --- IRB ---
        g_idx = self._normalize_interleaved()

        idxs_irb: List[int] = []
        now = 0  # accumulate total rotation (indices) for inverse

        for c in rand_idxs:
            idxs_irb.append(c)
            now = self.compose(c, now)
            idxs_irb.append(g_idx)
            now = self.compose(g_idx, now)

        if self.include_inverse:
            idxs_irb.append(self.inverse(now))

        return self.get_gates(idxs=idxs_irb)

    def _normalize_interleaved(self):
        
        interleaved = self.interleaved
        # case 1: direct index
        if isinstance(interleaved, int):
            if not (0 <= interleaved < 24):
                raise ValueError("interleaved clifford index must be in [0, 23].")
            return interleaved

        # case 2: label -> index
        if isinstance(interleaved, str):
            label = interleaved.strip()
            try:
                idx = self.cliff24.index(label)
            except ValueError as e:
                raise ValueError(f"Unknown Clifford label {label!r} for interleaved.") from e
            return idx

        raise ValueError(f"Unknown Clifford {interleaved}")

    def compose(self, i: int, j: int) -> int:
        """
        Group composition with matrix multiplication:
          rotation = rots[i] @ rots[j]
        (Meaning: do j, then do i on the Bloch vector.)
        """
        M = self.rots[i] @ self.rots[j]
        return self.rot2idx[tuple(M.flatten())]

    def inverse(self, i: int) -> int:
        """
        For rotations, inverse = transpose.
        """
        Minv = self.rots[i].T
        return self.rot2idx[tuple(Minv.flatten())]

    def clifford_label(self, idx: int) -> str:
        return self.cliff24[idx]

    # --- internals: Clifford set + lookup tables ----------------------------

    @property
    def R(self) -> Dict[str, np.ndarray]:
        # cached via property; matrices are small so it's fine.
        return {
            "I":    np.eye(3, dtype=int),

            "X":    np.array([[ 1, 0, 0],
                              [ 0,-1, 0],
                              [ 0, 0,-1]], dtype=int),          # Rx(pi)

            "Y":    np.array([[-1, 0, 0],
                              [ 0, 1, 0],
                              [ 0, 0,-1]], dtype=int),          # Ry(pi)

            "X/2":  np.array([[ 1, 0, 0],
                              [ 0, 0,-1],
                              [ 0, 1, 0]], dtype=int),          # Rx(+pi/2)

            "-X/2": np.array([[ 1, 0, 0],
                              [ 0, 0, 1],
                              [ 0,-1, 0]], dtype=int),          # Rx(-pi/2)

            "Y/2":  np.array([[ 0, 0, 1],
                              [ 0, 1, 0],
                              [-1, 0, 0]], dtype=int),          # Ry(+pi/2)

            "-Y/2": np.array([[ 0, 0,-1],
                              [ 0, 1, 0],
                              [ 1, 0, 0]], dtype=int),          # Ry(-pi/2)
        }

    @property
    def cliff24(self) -> List[str]:
        return [
            "I","X","Y","Y,X",
            "X/2,Y/2","X/2,-Y/2","-X/2,Y/2","-X/2,-Y/2",
            "Y/2,X/2","Y/2,-X/2","-Y/2,X/2","-Y/2,-X/2",
            "X/2","-X/2","Y/2","-Y/2",
            "-X/2,Y/2,X/2","-X/2,-Y/2,X/2",
            "X,Y/2","X,-Y/2","Y,X/2","Y,-X/2",
            "X/2,Y/2,X/2","X/2,-Y/2,X/2",
        ]

    def rot_of(self, seq: str) -> np.ndarray:
        parts = [p.strip() for p in seq.split(",") if p.strip()]
        M = np.eye(3, dtype=int)
        for p in parts:
            M = self.R[p] @ M
        return M

    @property
    def rots(self) -> List[np.ndarray]:
        return [self.rot_of(s) for s in self.cliff24]

    @property
    def rot2idx(self) -> Dict[Tuple[int, ...], int]:
        rots = self.rots
        rot2idx = {tuple(M.flatten()): i for i, M in enumerate(rots)}
        if len(rot2idx) != 24:
            raise ValueError(f"Clifford set not unique: got {len(rot2idx)} unique rotations, expected 24.")
        return rot2idx
    
    def _to_gate(self, seq: str):
        
        gates = []
        parts = [p.strip() for p in seq.split(",") if p.strip()]
        for p in parts:
            if p == 'I': continue
            name = p
            arg_value = []
            if p == 'X/2':
                name = 'RX'
                arg_value = [np.pi/2]
            elif p == '-X/2':
                name = 'RX'
                arg_value = [-np.pi/2]
            elif p == 'Y/2':
                name = 'RY'
                arg_value = [np.pi/2]
            elif p == '-Y/2':
                name = 'RY'
                arg_value = [-np.pi/2]
            gates.append((name, self.targets, arg_value))
        return gates
    
    @property
    def cgates(self):
        return [self._to_gate(s) for s in self.cliff24]
    
    def get_gates(self, idxs):
        gates = []
        for idx in idxs:
            gates.extend(self.cgates[idx])
        if self.add_measure:
            gates.append(('MEASURE', self.targets))
        return gates
                
    
    def rb_indices(self, m: int, k: int, run_seed):
        seed = self.derive_seed(run_seed=run_seed, m=m, k=k)
        rng = np.random.Generator(np.random.PCG64(seed))
        idxs = rng.integers(0, 24, size=m).tolist()

        if self.include_inverse:
            now = 0
            for c in idxs:
                now = self.compose(c, now)
            idxs.append(self.inverse(now))

        return idxs

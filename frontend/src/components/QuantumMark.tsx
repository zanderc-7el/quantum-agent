import { Atom } from "lucide-react";

/** Shared brand artwork: sidebar and experiment discussion use the same mark. */
export default function QuantumMark({ size = 28 }: { size?: number }) {
  return <span className="brand-mark" aria-hidden="true"><Atom size={size} /></span>;
}

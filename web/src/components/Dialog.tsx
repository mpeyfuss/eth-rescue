import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

export function Dialog({
  open,
  title,
  closeLabel = "Close",
  narrow = false,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  closeLabel?: string;
  narrow?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => opener?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className={narrow ? "modal-sheet narrow" : "modal-sheet"} onClick={(e) => e.stopPropagation()}>
        <div className="modal-bar">
          <strong>{title}</strong>
          <button ref={closeRef} type="button" className="icon-button" onClick={onClose} aria-label={closeLabel}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

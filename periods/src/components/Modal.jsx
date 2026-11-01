import { useEffect, useRef } from 'react';

// Native <dialog>: focus trapping, Escape and the backdrop come from the browser.
export default function Modal({ labelledBy, onClose, className = '', children }) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog.open) dialog.showModal();
    return () => dialog.open && dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${className}`}
      aria-labelledby={labelledBy}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      {children}
    </dialog>
  );
}

export function ConfirmDialog({ title, body, confirmLabel, danger = false, onConfirm, onCancel }) {
  return (
    <Modal labelledBy="confirm-title" onClose={onCancel}>
      <h2 id="confirm-title">{title}</h2>
      <p>{body}</p>
      <div className="row row--end">
        <button type="button" className="button button--quiet" onClick={onCancel} autoFocus>
          Cancel
        </button>
        <button type="button" className={`button${danger ? ' button--danger' : ''}`} onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

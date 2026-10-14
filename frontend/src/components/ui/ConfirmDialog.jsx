import Modal from "./Modal.jsx";
import Button from "./Button.jsx";

export default function ConfirmDialog({ title, message, confirmLabel = "Delete", onConfirm, onCancel }) {
  return (
    <Modal title={title} onClose={onCancel}>
      <p className="text-ui leading-relaxed text-ink-muted mb-6">{message}</p>
      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </Modal>
  );
}

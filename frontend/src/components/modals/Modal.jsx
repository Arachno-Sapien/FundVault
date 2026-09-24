export default function Modal({ open, id, title, children, onClose, large = false }) {
  return (
    <div className={`overlay ${open ? "open" : ""}`} id={id} onClick={e => e.target.id === id && onClose()}>
      <div className={`modal ${large ? "modal-lg" : ""}`}>
        <div className="modal-head">
          <span className="modal-title">{title}</span>
          <button className="close-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

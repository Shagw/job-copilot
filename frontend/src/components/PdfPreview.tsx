/** Shows a server-rendered PDF in the browser's built-in viewer. */
export function PdfPreview({ src, title }: { src: string; title: string }) {
  return (
    <div className="pdf-preview">
      {/* Fit page width and hide the thumbnail sidebar (Chrome/Edge/Firefox viewers). */}
      <iframe src={`${src}#view=FitH&navpanes=0`} title={title} />
      <p className="muted small">
        Preview not showing (some phones can't display PDFs inline)?{' '}
        <a href={src} target="_blank" rel="noopener noreferrer">
          Open the PDF
        </a>
      </p>
    </div>
  )
}

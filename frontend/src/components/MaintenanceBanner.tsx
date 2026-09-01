export function MaintenanceBanner({ message }: { message: string }) {
  return (
    <div className="maintenance-banner" role="status">
      <div>
        <strong>Database resting</strong>
        <span className="maintenance-banner-detail">{message}</span>
      </div>
    </div>
  );
}

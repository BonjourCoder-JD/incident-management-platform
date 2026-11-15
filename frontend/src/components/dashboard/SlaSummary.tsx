import type { SlaAnalytics, SlaComplianceCounts } from '../../api/types';
import { SEVERITY_LABELS } from '../../format';
import { MeterList } from './MeterList';
import { StatTile } from './StatTile';
import { formatCount, percent } from './format';

function onTime(counts: SlaComplianceCounts): { value: string; detail: string } {
  const finished = counts.met + counts.breached;
  return {
    value: finished === 0 ? '—' : percent(counts.met, finished),
    detail: `${formatCount(counts.met)} of ${formatCount(finished)} on time · ${formatCount(counts.outstanding)} outstanding`,
  };
}

/** SLA compliance for incidents created in the range (first period) and the current overdue backlog. */
export function SlaSummary({ sla, staff }: { sla: SlaAnalytics; staff: boolean }) {
  const response = onTime(sla.response);
  const resolution = onTime(sla.resolution);
  const overdue = sla.overdue_by_severity.reduce((sum, row) => sum + row.count, 0);
  const approaching = sla.approaching_by_severity.reduce((sum, row) => sum + row.count, 0);
  return (
    <section className="card sla-summary" aria-labelledby="sla-summary-heading">
      <h2 id="sla-summary-heading">SLA</h2>
      <p className="muted small card-caption">
        On-time rates cover incidents created in the selected dates (before any reopening). Overdue and approaching counts are for open incidents right now.
      </p>
      <div className="stat-row">
        <StatTile label="Responses on time" value={response.value} detail={response.detail} />
        <StatTile label="Resolutions on time" value={resolution.value} detail={resolution.detail} />
        <StatTile label="Overdue now" value={formatCount(overdue)} detail={`${formatCount(approaching)} approaching the deadline`} />
        {staff && sla.escalations !== null && (
          <StatTile label="Escalations" value={formatCount(sla.escalations)} detail="Recorded automatically in the selected dates" />
        )}
      </div>
      <MeterList
        label="Overdue now by severity"
        total={overdue}
        rows={sla.overdue_by_severity.map(row => ({ label: SEVERITY_LABELS[row.severity], value: row.count }))}
      />
      {(sla.response.not_tracked > 0 || sla.resolution.not_tracked > 0) && (
        <p className="coverage-note muted small">
          {formatCount(Math.max(sla.response.not_tracked, sla.resolution.not_tracked))} incidents in range started before SLA tracking and are not counted.
        </p>
      )}
    </section>
  );
}

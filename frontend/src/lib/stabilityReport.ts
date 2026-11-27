export interface StabilityReportFilter {startDate?:string;endDate?:string;chipVersionId?:string;resourceId?:string;sourceWorkflowId?:string}
export function stabilityReportPath(filters:StabilityReportFilter,format?:'html'|'download'){
 const query=new URLSearchParams();
 for(const key of ['startDate','endDate','chipVersionId','resourceId','sourceWorkflowId'] as const){const value=filters[key];if(value)query.set(key,value);}
 for(const key of ['startDate','endDate'] as const)if(filters[key]&&!/^\d{4}-\d{2}-\d{2}$/.test(filters[key]!))throw new Error('日期须为 YYYY-MM-DD');
 if(filters.startDate&&filters.endDate&&filters.startDate>filters.endDate)throw new Error('开始日期不能晚于结束日期');
 if(format)query.set('format','html');if(format==='download')query.set('download','1');
 return '/stability/report'+(query.size?'?'+query.toString():'');
}
export interface StabilityReportEvidence {acquiredAt:string;actualDriveHz?:number;attemptId:string;resultSha256:string}
export interface StabilityReportRow {scheduleId:string;scheduleName:string;number:number;state:string;startedAt:string;displayAt?:string;timeBasis?:string;frequencyHz:number|null;t1Seconds:number|null;referenceDeltaHz:number|null;adjacentDeltaHz:number|null;frequencyEvidence:StabilityReportEvidence|null;t1Evidence:StabilityReportEvidence|null;error?:string;evidenceIssues:string[]}
export interface StabilityRangeReport {choiceLabels?:Record<string,Record<string,string>>;capturedAt:string;snapshotSha256:string;filters:StabilityReportFilter;summary:{rounds:number;validFrequencyRounds:number;validT1Rounds:number};choices:{chipVersionIds:string[];resourceIds:string[];sourceWorkflowIds:string[]};groups:{id:string;chipVersionName?:string;deploymentVersionName?:string;sourceWorkflowName?:string;chipVersionId:string|null;deploymentVersionId:string|null;resourceId:string;sourceWorkflowId:string|null;identityStatus:string;rows:StabilityReportRow[]}[]}


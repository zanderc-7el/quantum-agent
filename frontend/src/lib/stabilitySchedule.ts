export const stabilityScheduleBounds={hours:{min:1/60,max:720},rounds:{min:1,max:1000},span:{min:1000,max:1e8},points:{min:3,max:161}};
export const stabilityClockPolicy='first_frequency_acquisition_completed_v1';
export function stabilityClockNote(schedule?:{clockPolicy?:string}){
 return !schedule||schedule.clockPolicy===stabilityClockPolicy?'间隔从每轮定频首测采集完成时起算。':'旧计划按轮次入队时间计算间隔。';
}
export function stabilityRoundClock(schedule:{clockPolicy?:string},round:{startedAt?:string;intervalAnchor?:{acquiredAt?:string}|null}){
 const measured=schedule.clockPolicy===stabilityClockPolicy;
 const at=measured?round.intervalAnchor?.acquiredAt:round.startedAt;
 return {at:at&&Number.isFinite(Date.parse(at))?at:null,label:measured?'首测采集完成':'入队时间（旧计划）'};
}
export function stabilityScheduleParameters(input:{hours:number;rounds:number;span:number;points:number}){
 const {hours,rounds,span,points}=input;
 if(!Number.isFinite(hours)||hours<1/60||hours>720)throw new Error('轮次间隔须为 1 分钟至 30 天');
 if(!Number.isInteger(rounds)||rounds<1||rounds>1000)throw new Error('最大轮次须为 1 至 1000 的整数');
 if(!Number.isFinite(span)||span<1000||span>1e8)throw new Error('精扫频宽须为 1000 至 100000000 Hz');
 if(!Number.isInteger(points)||points<3||points>161)throw new Error('频率点数须为 3 至 161 的整数');
 return {intervalSeconds:hours*3600,maxRounds:rounds,spanHz:span,points};
}

import test from 'node:test';
import assert from 'node:assert/strict';
import {plotAxis,discreteAxisIndices} from '../src/lib/sciencePlotAxis.ts';

test('narrow GHz scan labels remain distinct and state their display unit',()=>{
 const axis=plotAxis(7008600000,7010600000,'Hz');
 assert.equal(axis.unit,'GHz');
 const values=Array.from({length:5},(_,i)=>7008600000+i*500000);
 assert.equal(new Set(values.map(axis.format)).size,5);
 assert.equal(axis.format(values[0]),'7.0086');assert.equal(axis.format(values[4]),'7.0106');
});
test('small ADC values remain distinguishable while discrete axes use only true unique positions',()=>{
 const axis=plotAxis(.00484011,.00484015,'ADC');assert.equal(axis.unit,'ADC');
 assert.equal(new Set([.00484011,.00484012,.00484013,.00484014,.00484015].map(axis.format)).size,5);
 assert.deepEqual(discreteAxisIndices(3),[0,1,2]);assert.deepEqual(discreteAxisIndices(1),[0]);
 assert.equal(new Set(discreteAxisIndices(81)).size,5);
});

#!/usr/bin/env python

import json
import jenkins
import re
import datetime
from datetime import datetime, timedelta from pymongo import MongoClient from bson.objectid import ObjectId import requests import time import pytz import configparser import os

class JenkinsCollector():

   def __init__(self, cfg):
      global client
      self.client = jenkins.Jenkins(cfg['jenkins']['url'], username=cfg['jenkins']['username'], password=cfg['jenkins']['password'])

   def all_jobs(self):
      return self.client.get_jobs(folder_depth=1)

   def job_info(self, name):
      return self.client.get_job_info(name)

   def all_build_numbers(self, job_name):
      builds = []
      for build in self.job_info(job_name)['builds']:
         builds.append(build['number'])
      return builds

   def build_info(self, job_name, build_number):
      return self.client.get_build_info(job_name, int(build_number))

class JenkinsBuild():
   def __init__(self, build):
      self.build = build

   def artifacts(self):
      artifacts = []
      for artifact in self.build['artifacts']:
         if 'json' in artifact['fileName']:
            artifacts.append(artifact['relativePath'])
      return artifacts

   def buildUrl(self):
      return self.build['url']

   def building(self):
      return self.build['building']

class CucumberJsonParser():
   def __init__(self, json):
      self.json = json

   def testStartTime(self):
      date_object = datetime.strptime(self.json['start'], '%Y-%m-%dT%H:%M:%S.%fZ')
      date_object = date_object - timedelta(hours=4)
      return int(time.mktime(date_object.timetuple())) * 1000

   def testEndTime(self):
      date_object = datetime.strptime(self.json['end'], '%Y-%m-%dT%H:%M:%S.%fZ')
      date_object = date_object - timedelta(hours=4)
      return int(time.mktime(date_object.timetuple()) * 1000)

   def testSuites(self):
      return self.json['suites']

   def testSuccesses(self):
      return int(self.json['state']['passed'])
 
   def testFailures(self):
      return int(self.json['state']['failed'])

   def testSkipped(self):
      return int(self.json['state']['skipped'])

def convertTime(string):
   date_object = datetime.strptime(string, '%Y-%m-%dT%H:%M:%S.%fZ')
   date_object = date_object - timedelta(hours=4)
   return int(time.mktime(date_object.timetuple()) * 1000)   

def isNewCollection(db, job, build):
   query = {'description': job, 'executionId': build}
   item = db.test_results.find(query)
   if item.count() == 0:
      return True
   else:
      return False

def getBuildId(db, query):
   query = {'name': query}
   item = db.collectors.find(query)
   job_id = ''
   for i in item:
      job_id = i['_id']
   return str(job_id)      

def loadConfig():
   global cfg
   cfg = configparser.ConfigParser()
   cfg_path = unicode(os.path.dirname(os.path.realpath(__file__)) + '/hygieia_cucumber.properties', 'utf8')
   cfg.read(cfg_path)

def main():
   loadConfig()
   mongo_client = MongoClient(cfg['db']['host'])
   db = mongo_client.dashboard
   db.authenticate(cfg['db']['username'], cfg['db']['password'])

   collector = JenkinsCollector(cfg)
   for job in collector.all_jobs():
      if re.search(cfg['jenkins']['folder'], job['fullname']):
         builds = collector.all_build_numbers(job['fullname'])
         for b in builds:
            if isNewCollection(db, job['fullname'], b) == True:
               build = JenkinsBuild(collector.build_info(job['fullname'], b))
               if build.building() == False:   
                  if len(build.artifacts()) > 0:
                     data = {}
                     startTime = []
                     endTime = []
                     successes = []
                     failures = []
                     skipped = []
                     capabilities = {}
                     data['testCapabilities'] = []
                     capabilities = {}
                     for artifact in build.artifacts():
                        artifact_location = build.buildUrl() + 'artifact/' + artifact
                        s = requests.Session()
                        s.auth = (cfg['jenkins']['username'], cfg['jenkins']['password'])
                        r = s.get(artifact_location)
                        parser = CucumberJsonParser(r.json())
                        startTime.append(parser.testStartTime())
                        endTime.append(parser.testEndTime())
                        successes.append(parser.testSuccesses())            
                        failures.append(parser.testFailures())
                        skipped.append(parser.testSkipped())
                        capabilities['successTestSuiteCount'] = parser.testSuccesses()
                        capabilities['failedTestSuiteCount'] = parser.testFailures()
                        capabilities['skippedTestSuiteCount'] = parser.testSkipped()
                        capabilities['totalTestSuiteCount'] = parser.testSuccesses() + parser.testFailures() + parser.testSkipped()
                        capabilities['timestamp'] = int(min(startTime) * 1000)
                        capabilities['startTime'] = convertTime(parser.json['suites'][0]['start'])
                        capabilities['endTime'] = convertTime(parser.json['suites'][0]['end'])
                        capabilities['duration'] = capabilities['endTime'] - capabilities['startTime']
                        capabilities['description'] = parser.json['suites'][0]['name'][:40]
                        capabilities['type'] = 'Functional'
                        test_suites = {}
                        capabilities['testSuites'] = []
                        for suite in parser.json['suites'][1:]:
                           test_suites['id'] = str(ObjectId())
                           test_suites['description'] = suite['name'][:40] + '...'
                           test_suites['duration'] = suite['duration']
                           test_suites['startTime'] = convertTime(suite['start'])
                           test_suites['endTime'] = convertTime(suite['end'])
                           test_suites['successTestCaseCount'] = 0
                           test_suites['failedTestCaseCount'] = 0
                           test_suites['skippedTestCaseCount'] = 0
                           test_suites['unknownStatusCount'] = 0
                           test_cases = {}
                           for test in suite['tests']:
                              if test['state'] == 'pass':
                                 test_suites['successTestCaseCount'] += 1
                              elif test['state'] == 'fail':
                                 test_suites['failedTestCaseCount'] += 1
                              elif test['state'] == 'skipped':
                                 test_suites['skippedTestCaseCount'] += 1
                              else:
                                 test_suites['unknownStatusCount'] += 1
                           capabilities['testSuites'].append(test_suites)
                           test_suites = {}
                        data['testCapabilities'].append(capabilities)
                        capabilities = {}
                     data['executionId'] = b
                     data['endTime'] = max(endTime) 
                     data['startTime'] = min(startTime)
                     data['description'] = job['fullname']
                     data['successCount'] = sum(successes)
                     data['failureCount'] = sum(failures)
                     data['skippedCount'] = sum(skipped)
                     data['totalCount'] = sum([sum(successes), sum(failures), sum(skipped)])
                     data['duration'] = int(max(endTime) - min(startTime))
                     data['testJobName'] = job['fullname']
                     data['testJobUrl'] = cfg['jenkins']['url'] + '/job/'+ cfg['jenkins']['folder'] + '/job/' + job['fullname'].split('/')[1] + '/' + str(b) 
                     data['serverUrl'] = cfg['jenkins']['url'] 
                     data['testJobId'] = getBuildId(db, 'JenkinsCucumberTest')
                     data['type'] = 'Functional'
                     data['timestamp'] = int(min(startTime) * 1000)
                     data['niceName'] = 'WDIO cucumber'
                     data['targetEnvName'] = 'Dev'
                     data['targetAppName'] = job['fullname'].split('/')[1]
                     url = cfg['hygieia']['api_url'] +'/quality/test/'
                     data_json = json.dumps(data)
                     headers = {'Content-type': 'application/json'}
                     r = requests.post(url, data=data_json, headers=headers)

if __name__ == "__main__":
    main()


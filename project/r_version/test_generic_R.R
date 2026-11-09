args<-commandArgs(FALSE);setwd(dirname(normalizePath(sub('^--file=','',grep('^--file=',args,value=TRUE)[1]))))
dir.create('results',showWarnings=FALSE,recursive=TRUE)
source('config.R');source('pricing_engine.R');source('native_models.R');source('generic_motor.R')
set.seed(87);n<-600;d<-data.frame(policy_id=1:n,exposure=rep(c(.5,1),length.out=n),driver_age=sample(18:90,n,TRUE),log_density=runif(n,0,7),territory=sample(c('A','B'),n,TRUE))
d$claim_count<-rpois(n,.5);d$aggregate_loss<-d$claim_count*rgamma(n,shape=2,scale=500)
s<-motor_schema(c('driver_age','log_density'),'territory');checks<-0
for(family in c('glm','gam','gbm')) {
  m<-fit_generic_motor(d[1:450,],s,family);q<-d[451:600,];before<-predict_generic_loss(m,q);q$exposure<-2*q$exposure
  stopifnot(max(abs(predict_generic_loss(m,q)-2*before))<1e-8);checks<-checks+1
  m<-calibrate_generic_motor(m,d[451:600,]);stopifnot(abs(sum(predict_generic_loss(m,d[451:600,]))-sum(d$aggregate_loss[451:600]))<1e-7);checks<-checks+1
  stopifnot(all(quote_generic_motor(m,q,config$pricing,c(50,100))$quote>0));checks<-checks+1
}
write.csv(data.frame(checks_passed=checks,all_passed=TRUE,scope='Synthetic generic exposure/schema contracts, not a second real-portfolio validation'),'results/R_GENERIC_TEST_RESULTS.csv',row.names=FALSE)
cat(checks,'generic R exposure/schema checks passed.\n')

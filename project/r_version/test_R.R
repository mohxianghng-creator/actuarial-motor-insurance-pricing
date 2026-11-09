# Meaningful native-R contracts, including frozen-engine and business parity.
args<-commandArgs(FALSE);root<-dirname(normalizePath(sub('^--file=','',grep('^--file=',args,value=TRUE)[1])));setwd(root)
source('config.R');source('pricing_engine.R');source('native_models.R')
tests<-0L
check<-function(condition){stopifnot(condition);tests<<-tests+1L}
m<-read_portable_model('portable_model');q<-read.csv('data/quotes.csv',stringsAsFactors=FALSE)
out<-quote_portable(m,q);reference<-read.csv('portable_model/python_quote_detail.csv',stringsAsFactors=FALSE)
check(nrow(out)==10000);check(max(abs(out$quote-reference$quote))<1e-8)
check(all(out$manual_review==as.logical(reference$manual_review)));check(all(out$quote>=out$cost_floor-1e-10))
p<-m$parameters;p$margin_mode<-'flat_constant';p$flat_risk_margin<-20;p$fixed_expense<-10;p$variable_expense_ratio<-.2
v<-quote_prices(c(100,200),p,c(150,250));check(max(abs(v$quote-c(162.5,287.5)))<1e-10);check(all(abs(v$expected_contribution-20)<1e-10))
p$commercial_adjustment<--1000;v<-quote_prices(c(100,200),p,c(150,250));check(max(abs(v$quote-v$cost_floor))<1e-10)
p<-m$parameters;p$margin_mode<-'tiered_constant';check(all(money_margin(c(49,50,99,100),p,c(50,100))==c(10,20,20,35)))
p$margin_mode<-'smooth_tiered_constant';for(t in c(50,100))check(diff(range(quote_prices(c(t-1e-7,t+1e-7),p,c(50,100))$quote))<1e-5)
p$variable_expense_ratio<-1;check(inherits(try(quote_prices(100,p,c(50,100)),silent=TRUE),'try-error'))
y<-c(0,0,100,300);prediction<-rep(50,4);z<-calibrate_level(y,prediction,'credibility');check(z$factor>1&&z$factor<2)
check(calibrate_level(y,prediction,'full')$factor==2);check(calibrate_level(y,prediction,'none')$factor==1)
qsmall<-q[1:5,];qsmall$PolicyID<-10001:10005;qsmall$CarAge[1]<-0;qsmall$Brand[2]<-'FIAT';qsmall$Power[3]<-'UNSEEN_POWER';qsmall$CarAge[4]<-NA;qsmall$Density[5]<-0
edge<-quote_portable(m,qsmall);check(all(is.finite(edge$quote)));check(all(edge$manual_review[2:5]))
qduplicate<-rbind(q[1,],q[1,]);check(inherits(try(quote_portable(m,qduplicate),silent=TRUE),'try-error'))
c<-config$commercial;c$net_premium_ratio<-.85;commercial<-commercial_grid(out,c)
check(nrow(commercial$grid)==216);check(!commercial$feasible);check(is.na(commercial$selected_adjustment))
check(all(commercial$grid$expected_policies<=c$expected_capacity+1e-8))
raw<-read.csv('data/training_and_calibration.csv',stringsAsFactors=FALSE);sample<-raw[1:1500,]
e<-rep(c(.5,1),length.out=nrow(sample));fit<-fit_native_model('Frequency_GLM_Pooled',sample,config,exposure=e)
check(max(abs(predict_native_model(fit,sample,exposure=2*e)-2*predict_native_model(fit,sample,exposure=e)))<1e-8)
saveRDS(m,'results/test_model_roundtrip.rds');check(max(abs(quote_portable(readRDS('results/test_model_roundtrip.rds'),q[1:10,])$quote-out$quote[1:10]))<1e-10)
unlink('results/test_model_roundtrip.rds')
write.csv(data.frame(checks_passed=tests,all_passed=TRUE,exact_quote_max_error=max(abs(out$quote-reference$quote))), 'results/R_TEST_RESULTS.csv',row.names=FALSE)
cat(tests,'native R contract checks passed.\n')

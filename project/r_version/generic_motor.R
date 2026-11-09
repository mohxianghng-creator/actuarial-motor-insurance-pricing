# Bonus configurable exposure-aware GLM/GAM/GBM template.
# Source pricing_engine.R and native_models.R before this file.
motor_schema <- function(numeric_features,categorical_features,id="policy_id",exposure="exposure",count="claim_count",loss="aggregate_loss") {
  f<-c(numeric_features,categorical_features)
  if(!length(f)||anyDuplicated(f)||any(f%in%c(id,exposure,count,loss)))stop("Explicit risk features must exclude ID, exposure and outcomes")
  list(numeric=numeric_features,categorical=categorical_features,id=id,exposure=exposure,count=count,loss=loss)
}
generic_extract <- function(d,s,targets=FALSE) {
  required<-c(s$numeric,s$categorical,s$id,s$exposure,if(targets)c(s$count,s$loss))
  if(!all(required%in%names(d)))stop("Missing schema columns")
  if(anyNA(d[[s$id]])||anyDuplicated(d[[s$id]]))stop("IDs must be complete and unique")
  e<-as.numeric(d[[s$exposure]]);if(any(!is.finite(e))||any(e<=0))stop("Exposure must be positive and finite")
  if(targets){n<-d[[s$count]];y<-d[[s$loss]];if(any(!is.finite(n))||any(!is.finite(y))||any(n<0)||any(y<0)||any(n!=floor(n))||any(n==0&y>0)||any(n>0&y==0))stop("Reconcile claim targets including zero-cost claims")}
  e
}
generic_pre_fit <- function(d,s,family) {
  raw<-if(length(s$numeric))as.matrix(d[s$numeric]) else matrix(numeric(),nrow(d),0)
  storage.mode(raw)<-"double";med<-if(ncol(raw))apply(raw,2,median,na.rm=TRUE) else numeric()
  if(any(!is.finite(med)))stop("Completely missing numeric feature")
  for(j in seq_len(ncol(raw))){if(any(!is.finite(raw[!is.na(raw[,j]),j])))stop("Nonfinite feature");raw[is.na(raw[,j]),j]<-med[j]}
  knots<-if(family=="gam")lapply(seq_len(ncol(raw)),function(j){lo<-min(raw[,j]);hi<-max(raw[,j]);if(lo==hi){lo<-lo-.5;hi<-hi+.5};h<-(hi-lo)/4;c(lo-h*(3:1),seq(lo,hi,length.out=5),hi+h*(1:3))}) else NULL
  num<-if(family=="gam"&&ncol(raw))do.call(cbind,lapply(seq_len(ncol(raw)),function(j)spline_basis(raw[,j],knots[[j]]))) else raw
  center<-colMeans(num);scale<-sqrt(colMeans(sweep(num,2,center,"-")^2));scale[scale==0]<-1
  list(schema=s,family=family,median=med,knots=knots,center=center,scale=scale,levels=lapply(d[s$categorical],function(x)sort(unique(ifelse(is.na(x),"Missing",as.character(x))))))
}
generic_pre_transform <- function(p,d) {
  s<-p$schema;raw<-if(length(s$numeric))as.matrix(d[s$numeric]) else matrix(numeric(),nrow(d),0);storage.mode(raw)<-"double"
  for(j in seq_len(ncol(raw))){if(any(!is.finite(raw[!is.na(raw[,j]),j])))stop("Nonfinite feature");raw[is.na(raw[,j]),j]<-p$median[j]}
  num<-if(p$family=="gam"&&ncol(raw))do.call(cbind,lapply(seq_len(ncol(raw)),function(j)spline_basis(raw[,j],p$knots[[j]]))) else raw
  num<-sweep(sweep(num,2,p$center,"-"),2,p$scale,"/")
  cats<-if(length(s$categorical))do.call(cbind,lapply(s$categorical,function(name){v<-as.character(d[[name]]);v[is.na(v)]<-"Missing";sapply(p$levels[[name]],function(level)as.numeric(v==level))})) else matrix(numeric(),nrow(d),0)
  z<-cbind(num,cats);colnames(z)<-paste0('z',seq_len(ncol(z)));z
}
fit_generic_motor <- function(d,schema,family="glm",alpha=.1) {
  if(!family%in%c("glm","gam","gbm"))stop("Choose glm/gam/gbm")
  e<-generic_extract(d,schema,TRUE);n<-d[[schema$count]];y<-d[[schema$loss]];positive<-n>0
  if(sum(positive)<30)stop("Too few positive policies")
  pre<-generic_pre_fit(d,schema,family);z<-generic_pre_transform(pre,d)
  freq<-if(family=="gbm")fit_newton_gbm(z,n/e,e,min_leaf=30) else fit_log_glm(z,n/e,e,'poisson',alpha)
  sev<-if(family=="gbm")fit_newton_gbm(z[positive,,drop=FALSE],y[positive]/n[positive],n[positive],'gamma',min_leaf=30) else fit_log_glm(z[positive,,drop=FALSE],y[positive]/n[positive],n[positive],'gamma',alpha)
  list(schema=schema,family=family,pre=pre,frequency=freq,severity=sev,calibration_factor=1)
}
predict_generic_loss <- function(m,d) {
  e<-generic_extract(d,m$schema);z<-generic_pre_transform(m$pre,d)
  f<-if(m$family=='gbm')predict_newton_gbm(m$frequency,z) else predict_log_glm(m$frequency,z)
  s<-if(m$family=='gbm')predict_newton_gbm(m$severity,z) else predict_log_glm(m$severity,z)
  f*s*e*m$calibration_factor
}
calibrate_generic_motor <- function(m,calibration_data) {
  generic_extract(calibration_data,m$schema,TRUE)
  raw<-predict_generic_loss(m,calibration_data)/m$calibration_factor
  m$calibration_factor<-calibrate_level(calibration_data[[m$schema$loss]],raw,'full')$factor
  m
}
quote_generic_motor <- function(m,d,pricing,locked_thresholds) {
  out<-cbind(PolicyID=d[[m$schema$id]],quote_prices(predict_generic_loss(m,d),pricing,locked_thresholds))
  out$usage_status<-'GENERIC_TEMPLATE_REQUIRES_PORTFOLIO_VALIDATION';out
}
